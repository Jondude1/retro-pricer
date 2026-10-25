"""
Retro Pricer — sourcing calculator for retro game resellers.
Flask web app, mobile-first; wrapped by the Android app as a WebView.
"""
import os, io, re, json, time, base64
from concurrent.futures import ThreadPoolExecutor
import requests
import certifi
from flask import Flask, render_template, request, jsonify, send_from_directory
import scraper, db, profit, repro

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5")
APP_VERSION = "2.0.0"

db.init()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_anthropic_key():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    try:  # local dev fallback
        cfg_path = os.path.join(os.path.expanduser("~"), ".openclaw", "openclaw.json")
        with open(cfg_path) as f:
            return json.load(f)["models"]["providers"]["anthropic"]["apiKey"]
    except Exception:
        return None


_DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def _device_id():
    d = request.headers.get("X-Device-Id", "")
    return d if _DEVICE_RE.match(d) else None


def _prepare_image(file_storage):
    """Downscale phone photos so they stay well under the API's image size limit."""
    raw = file_storage.read()
    if not raw:
        return None, None
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((1568, 1568))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return base64.standard_b64encode(buf.getvalue()).decode(), "image/jpeg"
    except Exception:
        mime = file_storage.mimetype or "image/jpeg"
        return base64.standard_b64encode(raw).decode(), mime


def _claude_json(content, system=None, max_tokens=700):
    """Call Claude and parse a JSON object out of the reply."""
    api_key = _get_anthropic_key()
    if not api_key:
        raise RuntimeError("Anthropic API key not configured")
    body = {"model": CLAUDE_MODEL, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}]}
    if system:
        body["system"] = system
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json=body, verify=certifi.where(), timeout=45,
    )
    if not resp.ok:
        raise RuntimeError(f"Claude API error {resp.status_code}: {resp.text[:200]}")
    text = "".join(b.get("text", "") for b in resp.json().get("content", []) if b.get("type") == "text")
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError(f"Unexpected response from Claude: {text[:200]}")
    return json.loads(m.group(0))


def lookup_prices(pc_console, slug, name="", console_key="", pc_id=""):
    """Market prices + DK Oldies data for one game (cached)."""
    cached = db.get_cached(pc_console, slug)
    if cached:
        return cached

    console_key = console_key or scraper.PC_TO_KEY.get(pc_console, "")
    pc_data = scraper.get_pricecharting_prices(pc_console, slug, pc_id)
    title = pc_data.get("title") or name
    console_display = scraper.CONSOLES.get(console_key, {}).get("name", "")
    dk_data = scraper.get_dkoldies_price(name or title, console_display)
    dk_buy = scraper.get_dkoldies_buy_price(name or title, console_key)
    media = scraper.CONSOLES.get(console_key, {}).get("media", "")

    result = {
        "pc_console":       pc_console,
        "slug":             slug,
        "pc_id":            pc_id,
        "console_key":      console_key,
        "console_name":     console_display,
        "media":            media,
        "title":            title,
        "pc_url":           pc_data.get("url", ""),
        "ebay_sold_url":    scraper.ebay_sold_url(title, console_display),
        "dk_url":           (dk_data or {}).get("url", ""),
        "prices":           pc_data.get("prices", {}),
        "price_error":      pc_data.get("error"),
        "price_source":     scraper.price_source(),
        "dk_price":         (dk_data or {}).get("price_cents"),
        "dk_buy_price":     (dk_buy or {}).get("buy_cents"),
        "dk_buy_name":      (dk_buy or {}).get("name"),
        "dk_buy_condition": (dk_buy or {}).get("buy_condition"),
        "fetched_at":       int(time.time()),
    }
    result["repro"] = repro.assess(title, console_key, media, result["prices"].get("loose"))
    db.save(result)
    return result


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", consoles=scraper.CONSOLES, version=APP_VERSION,
                           fee_defaults=profit.DEFAULT_SETTINGS)


@app.route("/privacy")
def privacy():
    return render_template("privacy.html", updated="October 24, 2026")


@app.route("/sw.js")
def service_worker():
    # Served from the root so its scope covers the whole app
    resp = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/health")
def health():
    return jsonify({"ok": True, "version": APP_VERSION, "price_source": scraper.price_source(),
                    "buylist": scraper.buylist_status(), "claude": bool(_get_anthropic_key())})


# ---------------------------------------------------------------------------
# Price lookups
# ---------------------------------------------------------------------------

@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    console_key = request.args.get("console", "").strip()
    if not q:
        return jsonify([])
    db.log_search(q, console_key)
    return jsonify(scraper.search_pricecharting(q, console_key or None))


@app.route("/prices")
def prices():
    pc_console = request.args.get("pc_console", "").strip()
    slug = request.args.get("slug", "").strip()
    if not pc_console or not slug:
        return jsonify({"error": "Missing params"}), 400
    return jsonify(lookup_prices(pc_console, slug,
                                 name=request.args.get("name", "").strip(),
                                 console_key=request.args.get("console_key", "").strip(),
                                 pc_id=request.args.get("pc_id", "").strip()))


@app.route("/api/evaluate", methods=["POST"])
def evaluate():
    """Profit per channel for a game at an asking price, using the user's fee settings."""
    body = request.get_json(silent=True) or {}
    market = {k: body.get("prices", {}).get(k) for k in ("loose", "cib", "new")}
    asking = body.get("asking_cents")
    return jsonify(profit.evaluate(
        market,
        asking_cents=int(asking) if asking not in (None, "") else None,
        buylist_cents=body.get("buylist_cents"),
        grade=body.get("grade"),
        settings=body.get("settings"),
        conditions=[body["condition"]] if body.get("condition") else None,
    ))


# ---------------------------------------------------------------------------
# Camera: single game
# ---------------------------------------------------------------------------

SCAN_SYSTEM = (
    "You are a retro video game condition expert helping a reseller evaluate games at pawn shops, "
    "thrift stores and garage sales. Identify the game and assess its physical condition from photos. "
    "Be accurate: the user is making a buy decision. If you cannot confidently identify the game or "
    "assess condition, ask for one specific additional photo. Flag anything that suggests a reproduction "
    "or counterfeit (label print quality, shell color, wrong screws, region mismatch)."
)

SCAN_SCHEMA = """{{
  "identified": true or false,
  "game_name": "exact game title or null",
  "console_key": one of [{consoles}] or null,
  "console_display": "human-readable console name or null",
  "condition": "loose" | "cib" | "cib_incomplete" | "new_sealed" | "damaged" | "unknown",
  "condition_grade": "Excellent" | "Good" | "Fair" | "Poor" | null,
  "condition_notes": "brief description of physical condition: label wear, case cracks, yellowing, etc.",
  "authenticity_notes": "anything suggesting a reproduction, or null",
  "confidence": "high" | "medium" | "low",
  "needs_more_photos": true or false,
  "photo_request": "specific instruction for the next photo, or null",
  "resale_notes": "1-2 sentences on sellability, common vs rare, demand level"
}}"""


def _consoles_list():
    return ", ".join(f'"{k}"' for k in scraper.CONSOLES)


@app.route("/scan", methods=["POST"])
@app.route("/scan/followup", methods=["POST"])
def scan():
    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400
    b64, mime = _prepare_image(request.files["image"])
    if not b64:
        return jsonify({"error": "Empty image"}), 400

    try:
        prev = json.loads(request.form.get("context", "") or "{}")
    except Exception:
        prev = {}

    schema = SCAN_SCHEMA.format(consoles=_consoles_list())
    if prev:
        prompt = (f"This is a follow-up photo for: {prev.get('game_name', 'unknown game')} "
                  f"({prev.get('console_display', 'unknown console')}).\n"
                  f"Previous assessment: condition={prev.get('condition', 'unknown')}, "
                  f"grade={prev.get('condition_grade')}, you requested: \"{prev.get('photo_request', 'additional view')}\".\n\n"
                  f"Combine both photos into a final assessment. Respond ONLY with JSON:\n{schema}")
    else:
        prompt = (f"Analyze this image of a video game or console. Respond ONLY with JSON:\n{schema}\n\n"
                  "Condition definitions:\n- loose: cartridge or disc only\n- cib: box + manual + game\n"
                  "- cib_incomplete: has box but missing manual, or vice versa\n- new_sealed: factory sealed\n"
                  "- damaged: physical damage that affects value\n- unknown: cannot tell from this photo")
    try:
        result = _claude_json([
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
            {"type": "text", "text": prompt},
        ], system=SCAN_SYSTEM)
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    return jsonify(result)


# ---------------------------------------------------------------------------
# Camera: whole lot / pile of games
# ---------------------------------------------------------------------------

LOT_PROMPT = """This photo shows a lot of video games for sale (a pile, a shelf, or a box).
List every game you can identify. Respond ONLY with JSON:
{{
  "items": [
    {{"game_name": "exact title", "console_key": one of [{consoles}] or null,
      "condition": "loose" | "cib" | "new_sealed" | "unknown", "quantity": 1,
      "confidence": "high" | "medium" | "low"}}
  ],
  "unidentified_count": number of items you can see but cannot identify,
  "notes": "one sentence about the lot overall (condition, anything notable), or null"
}}
Only include games you can actually read or recognize. Do not guess titles."""

MAX_LOT_ITEMS = 30


def _price_lot_item(item, settings):
    name = (item.get("game_name") or "").strip()
    ck = item.get("console_key") or ""
    out = {"game_name": name, "console_key": ck, "condition": item.get("condition") or "unknown",
           "quantity": max(1, int(item.get("quantity") or 1)), "confidence": item.get("confidence")}
    if not name:
        return out
    matches = scraper.search_pricecharting(name, ck or None, with_buylist=False)
    if not matches:
        out["error"] = "no price match"
        return out
    m = matches[0]
    data = lookup_prices(m["pc_console"], m["slug"], m["name"], ck or m.get("console_key", ""), m.get("pc_id", ""))
    cond = {"cib": "cib", "new_sealed": "new"}.get(out["condition"], "loose")
    market = data["prices"].get(cond) or data["prices"].get("loose")
    ev = profit.evaluate({cond: market} if market else {}, buylist_cents=data.get("dk_buy_price"), settings=settings)
    best = ev["best"]
    out.update(
        title=data["title"], pc_console=data["pc_console"], slug=data["slug"], pc_id=data.get("pc_id", ""),
        price_condition=cond, market_cents=market, dk_buy_cents=data.get("dk_buy_price"),
        best_channel=best["channel"] if best else None,
        best_net_cents=best["net_cents"] if best else None,
        max_offer_cents=best["max_offer_cents"] if best else 0,
        repro=data.get("repro"),
    )
    return out


@app.route("/scan/lot", methods=["POST"])
def scan_lot():
    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400
    b64, mime = _prepare_image(request.files["image"])
    if not b64:
        return jsonify({"error": "Empty image"}), 400
    try:
        settings = json.loads(request.form.get("settings", "") or "{}")
    except Exception:
        settings = {}
    try:
        ident = _claude_json([
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
            {"type": "text", "text": LOT_PROMPT.format(consoles=_consoles_list())},
        ], max_tokens=2000)
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    raw_items = [i for i in ident.get("items", []) if i.get("game_name")][:MAX_LOT_ITEMS]
    workers = 1 if scraper.PRICECHARTING_TOKEN else 4  # API is rate limited to 1/sec
    with ThreadPoolExecutor(max_workers=workers) as pool:
        items = list(pool.map(lambda i: _price_lot_item(i, settings), raw_items))
    return jsonify(lot_totals(items, ident))


def lot_totals(items, ident=None):
    ident = ident or {}
    priced = [i for i in items if i.get("market_cents") or i.get("dk_buy_cents")]
    return {
        "items": items,
        "unidentified_count": int(ident.get("unidentified_count") or 0),
        "notes": ident.get("notes"),
        "total_market_cents": sum((i.get("market_cents") or 0) * i["quantity"] for i in priced),
        "total_net_cents": sum((i.get("best_net_cents") or 0) * i["quantity"] for i in priced),
        "max_offer_cents": sum((i.get("max_offer_cents") or 0) * i["quantity"] for i in priced),
        "buylist_floor_cents": sum((i.get("dk_buy_cents") or 0) * i["quantity"] for i in priced),
        "priced_count": len(priced),
    }


@app.route("/api/lot/price", methods=["POST"])
def price_lot_manual():
    """Price a hand-entered list of games (same output as /scan/lot)."""
    body = request.get_json(silent=True) or {}
    raw_items = [i for i in body.get("items", []) if i.get("game_name")][:MAX_LOT_ITEMS]
    workers = 1 if scraper.PRICECHARTING_TOKEN else 4
    with ThreadPoolExecutor(max_workers=workers) as pool:
        items = list(pool.map(lambda i: _price_lot_item(i, body.get("settings")), raw_items))
    return jsonify(lot_totals(items))


# ---------------------------------------------------------------------------
# Inventory / profit tracker (per device, no account needed)
# ---------------------------------------------------------------------------

def _need_device():
    d = _device_id()
    if not d:
        return None, (jsonify({"error": "missing device id"}), 400)
    return d, None


@app.route("/api/inventory", methods=["GET"])
def inventory_list():
    d, err = _need_device()
    if err:
        return err
    items = db.inventory_list(d)
    return jsonify({"items": items, "summary": db.inventory_summary(items)})


@app.route("/api/inventory", methods=["POST"])
def inventory_add():
    d, err = _need_device()
    if err:
        return err
    try:
        return jsonify(db.inventory_add(d, request.get_json(silent=True) or {})), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/inventory/<int:item_id>", methods=["PATCH"])
def inventory_update(item_id):
    d, err = _need_device()
    if err:
        return err
    item = db.inventory_update(d, item_id, request.get_json(silent=True) or {})
    return (jsonify(item), 200) if item else (jsonify({"error": "not found"}), 404)


@app.route("/api/inventory/<int:item_id>", methods=["DELETE"])
def inventory_delete(item_id):
    d, err = _need_device()
    if err:
        return err
    return jsonify({"deleted": db.inventory_delete(d, item_id)})


@app.route("/api/inventory.csv")
def inventory_csv():
    import csv
    d = request.args.get("device", "")
    if not _DEVICE_RE.match(d):
        return jsonify({"error": "missing device id"}), 400
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["title", "console_key", "condition", "paid", "source", "bought", "market", "channel",
            "sold", "fees", "shipping", "sold_on", "profit", "notes"]
    w.writerow(cols)

    def money(c):
        return "" if c is None else f"{c / 100:.2f}"

    def day(t):
        return time.strftime("%Y-%m-%d", time.localtime(t)) if t else ""

    for i in db.inventory_list(d):
        p = None
        if i.get("sold_cents") is not None:
            p = i["sold_cents"] - (i["paid_cents"] or 0) - (i.get("fees_cents") or 0) - (i.get("ship_cents") or 0)
        w.writerow([i["title"], i["console_key"], i["condition"], money(i["paid_cents"]), i["source"],
                    day(i["bought_at"]), money(i["market_cents"]), i["channel"], money(i["sold_cents"]),
                    money(i["fees_cents"]), money(i["ship_cents"]), day(i["sold_at"]), money(p), i["notes"]])
    return app.response_class(buf.getvalue(), mimetype="text/csv",
                              headers={"Content-Disposition": "attachment; filename=retro-pricer-inventory.csv"})


# ---------------------------------------------------------------------------
# eBay local pickup
# ---------------------------------------------------------------------------

@app.route("/api/local-deals")
def api_local_deals():
    zip_code = request.args.get("zip", "").strip()
    if not re.match(r"^\d{5}$", zip_code):
        return jsonify({"error": "Enter a 5-digit ZIP code"}), 400
    radius = request.args.get("radius", 25, type=int)
    query = request.args.get("q", "retro video games").strip()
    return jsonify(scraper.search_ebay_local(zip_code, radius, query))


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=bool(os.environ.get("FLASK_DEBUG")))
