"""
SQLite storage: price cache, search log, and per-device inventory.

Set DB_PATH to a persistent disk (e.g. /var/data/retro_pricer.db on Render);
the default lives next to the code and is wiped on every redeploy.
"""
import sqlite3, os, time, json

DB_PATH = os.environ.get("DB_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "retro_pricer.db")
CACHE_TTL = int(os.environ.get("PRICE_CACHE_TTL", 6 * 3600))  # PriceCharting updates daily

INVENTORY_FIELDS = ["title", "console_key", "pc_console", "slug", "condition", "paid_cents", "source",
                    "bought_at", "market_cents", "listed_cents", "channel", "sold_cents",
                    "fees_cents", "ship_cents", "sold_at", "notes"]


def init():
    d = os.path.dirname(DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = _conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS price_cache_v2 (
            pc_console  TEXT NOT NULL,
            slug        TEXT NOT NULL,
            payload     TEXT NOT NULL,
            updated_at  INTEGER NOT NULL,
            PRIMARY KEY (pc_console, slug)
        );
        CREATE TABLE IF NOT EXISTS search_log (
            id          INTEGER PRIMARY KEY,
            query       TEXT,
            console_key TEXT,
            searched_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS inventory (
            id           INTEGER PRIMARY KEY,
            device_id    TEXT NOT NULL,
            title        TEXT NOT NULL,
            console_key  TEXT,
            pc_console   TEXT,
            slug         TEXT,
            condition    TEXT,
            paid_cents   INTEGER NOT NULL DEFAULT 0,
            source       TEXT,
            bought_at    INTEGER,
            market_cents INTEGER,
            listed_cents INTEGER,
            channel      TEXT,
            sold_cents   INTEGER,
            fees_cents   INTEGER,
            ship_cents   INTEGER,
            sold_at      INTEGER,
            notes        TEXT,
            created_at   INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_log_time ON search_log(searched_at);
        CREATE INDEX IF NOT EXISTS idx_inv_device ON inventory(device_id, created_at);
    """)
    conn.commit()
    conn.close()


# ── Price cache ────────────────────────────────────────────────────────────

def get_cached(pc_console, slug):
    conn = _conn()
    row = conn.execute(
        "SELECT payload, updated_at FROM price_cache_v2 WHERE pc_console=? AND slug=? AND updated_at > ?",
        (pc_console, slug, int(time.time()) - CACHE_TTL),
    ).fetchone()
    conn.close()
    if not row:
        return None
    data = json.loads(row["payload"])
    data["cached_at"] = row["updated_at"]
    return data


def save(data):
    if not data.get("prices"):
        return  # don't cache failed lookups
    conn = _conn()
    conn.execute(
        "INSERT OR REPLACE INTO price_cache_v2 (pc_console, slug, payload, updated_at) VALUES (?,?,?,?)",
        (data["pc_console"], data["slug"], json.dumps(data), int(time.time())),
    )
    conn.commit()
    conn.close()


def log_search(query, console_key):
    conn = _conn()
    conn.execute("INSERT INTO search_log (query, console_key, searched_at) VALUES (?,?,?)",
                 (query, console_key, int(time.time())))
    conn.commit()
    conn.close()


# ── Inventory ──────────────────────────────────────────────────────────────

def _clean(fields):
    out = {}
    for k in INVENTORY_FIELDS:
        if k in fields:
            v = fields[k]
            if k.endswith("_cents") or k.endswith("_at"):
                v = None if v in (None, "") else int(v)
            elif v is not None:
                v = str(v)[:500]
            out[k] = v
    return out


def inventory_list(device_id):
    conn = _conn()
    rows = conn.execute("SELECT * FROM inventory WHERE device_id=? ORDER BY created_at DESC", (device_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def inventory_add(device_id, fields):
    f = _clean(fields)
    if not f.get("title"):
        raise ValueError("title is required")
    f.setdefault("paid_cents", 0)
    f.setdefault("bought_at", int(time.time()))
    cols = ["device_id", "created_at"] + list(f.keys())
    vals = [device_id, int(time.time())] + list(f.values())
    conn = _conn()
    cur = conn.execute(f"INSERT INTO inventory ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
    conn.commit()
    row = conn.execute("SELECT * FROM inventory WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return dict(row)


def inventory_update(device_id, item_id, fields):
    f = _clean(fields)
    if not f:
        return inventory_get(device_id, item_id)
    sets = ",".join(f"{k}=?" for k in f)
    conn = _conn()
    conn.execute(f"UPDATE inventory SET {sets} WHERE id=? AND device_id=?", list(f.values()) + [item_id, device_id])
    conn.commit()
    conn.close()
    return inventory_get(device_id, item_id)


def inventory_get(device_id, item_id):
    conn = _conn()
    row = conn.execute("SELECT * FROM inventory WHERE id=? AND device_id=?", (item_id, device_id)).fetchone()
    conn.close()
    return dict(row) if row else None


def inventory_delete(device_id, item_id):
    conn = _conn()
    cur = conn.execute("DELETE FROM inventory WHERE id=? AND device_id=?", (item_id, device_id))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def inventory_summary(items):
    sold = [i for i in items if i.get("sold_cents") is not None]
    unsold = [i for i in items if i.get("sold_cents") is None]
    revenue = sum(i["sold_cents"] for i in sold)
    costs = sum((i.get("paid_cents") or 0) + (i.get("fees_cents") or 0) + (i.get("ship_cents") or 0) for i in sold)
    days = [(i["sold_at"] - i["bought_at"]) / 86400 for i in sold if i.get("sold_at") and i.get("bought_at")]
    return {
        "items": len(items),
        "sold_count": len(sold),
        "unsold_count": len(unsold),
        "spent_cents": sum(i.get("paid_cents") or 0 for i in items),
        "revenue_cents": revenue,
        "profit_cents": revenue - costs,
        "unsold_cost_cents": sum(i.get("paid_cents") or 0 for i in unsold),
        "unsold_market_cents": sum(i.get("market_cents") or 0 for i in unsold),
        "avg_days_to_sell": round(sum(days) / len(days), 1) if days else None,
    }


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn
