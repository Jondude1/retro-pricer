"""
SQLite price cache. TTL: 24 hours.
"""
import sqlite3, os, time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "retro_pricer.db")
CACHE_TTL = 0  # Always fetch fresh — prices update frequently


def init():
    conn = _conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS price_cache (
            id           INTEGER PRIMARY KEY,
            pc_console   TEXT NOT NULL,
            slug         TEXT NOT NULL,
            game_title   TEXT,
            loose_price  INTEGER,
            cib_price    INTEGER,
            new_price    INTEGER,
            graded_price INTEGER,
            dk_price     INTEGER,
            pc_url       TEXT,
            dk_url       TEXT,
            updated_at   INTEGER NOT NULL,
            UNIQUE(pc_console, slug)
        );
        CREATE TABLE IF NOT EXISTS search_log (
            id          INTEGER PRIMARY KEY,
            query       TEXT,
            console_key TEXT,
            searched_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cache_updated ON price_cache(updated_at);
        CREATE INDEX IF NOT EXISTS idx_log_time ON search_log(searched_at);
    """)
    conn.commit()
    conn.close()


def get_cached(pc_console, slug):
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM price_cache WHERE pc_console=? AND slug=? AND updated_at > ?",
        (pc_console, slug, int(time.time()) - CACHE_TTL),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save(data):
    p = data.get("prices", {})
    conn = _conn()
    conn.execute("""
        INSERT INTO price_cache
            (pc_console, slug, game_title, loose_price, cib_price, new_price,
             graded_price, dk_price, pc_url, dk_url, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(pc_console, slug) DO UPDATE SET
            game_title=excluded.game_title,
            loose_price=excluded.loose_price,
            cib_price=excluded.cib_price,
            new_price=excluded.new_price,
            graded_price=excluded.graded_price,
            dk_price=excluded.dk_price,
            pc_url=excluded.pc_url,
            dk_url=excluded.dk_url,
            updated_at=excluded.updated_at
    """, (
        data.get("pc_console"), data.get("slug"), data.get("title"),
        p.get("loose"), p.get("cib"), p.get("new"), p.get("graded"),
        data.get("dk_price"), data.get("pc_url"), data.get("dk_url"),
        int(time.time()),
    ))
    conn.commit()
    conn.close()


def log_search(query, console_key):
    conn = _conn()
    conn.execute(
        "INSERT INTO search_log (query, console_key, searched_at) VALUES (?,?,?)",
        (query, console_key, int(time.time())),
    )
    conn.commit()
    conn.close()


def recent_lookups(limit=8):
    conn = _conn()
    rows = conn.execute("""
        SELECT pc.game_title, pc.pc_console, pc.slug, pc.loose_price, pc.cib_price, pc.updated_at
        FROM price_cache pc
        ORDER BY pc.updated_at DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
