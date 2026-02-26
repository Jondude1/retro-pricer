"""
Retro Game Price Checker
Flask web app — mobile-first, works on phone at pawn shops.
"""
import os, json, base64, time
import requests
import certifi
from flask import Flask, render_template, request, jsonify
import scraper, db

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB max upload

# Init DB on startup (works for both gunicorn and direct run)
db.init()

# ---------------------------------------------------------------------------
# Anthropic API key — set ANTHROPIC_API_KEY env var on Render.
# Falls back to reading from local openclaw.json for dev.
# ---------------------------------------------------------------------------
def _get_anthropic_key():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    try:
        cfg_path = os.path.join(os.path.expanduser("~"), ".openclaw", "openclaw.json")
        with open(cfg_path) as f:
            cfg = json.load(f)
        return cfg["models"]["providers"]["anthropic"]["apiKey"]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    history = db.recent_lookups(8)
    return render_template("index.html", consoles=scraper.CONSOLES, history=history)


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    console_key = request.args.get("console", "").strip()
    if not q:
        return jsonify([])
    db.log_search(q, console_key)
    results = scraper.search_pricecharting(q, console_key or None)
    return jsonify(results)


@app.route("/prices")
def prices():
    pc_console  = request.args.get("pc_console", "").strip()
    slug        = request.args.get("slug", "").strip()
    game_name   = request.args.get("name", "").strip()
    console_key = request.args.get("console_key", "").strip()

    if not pc_console or not slug:
        return jsonify({"error": "Missing params"}), 400

    # Return from cache if fresh
    cached = db.get_cached(pc_console, slug)
    if cached:
        return jsonify(cached)

    # Fetch live
    pc_data = scraper.get_pricecharting_prices(pc_console, slug)
    title   = pc_data.get("title", game_name)
    console_display = scraper.CONSOLES.get(console_key, {}).get("name", "")
    dk_data     = scraper.get_dkoldies_price(game_name or title, console_display)
    dk_buy_data = scraper.get_dkoldies_buy_price(game_name or title)

    result = {
        "pc_console":    pc_console,
        "slug":          slug,
        "title":         title,
        "pc_url":        pc_data.get("url", ""),
        "dk_url":        dk_data.get("url", "") if dk_data else "",
        "prices":        pc_data.get("prices", {}),
        "dk_price":      dk_data.get("price_cents") if dk_data else None,
        "dk_buy_price":  dk_buy_data.get("buy_cents") if dk_buy_data else None,
        "dk_buy_name":   dk_buy_data.get("name") if dk_buy_data else None,
    }

    db.save(result)
    return jsonify(result)


@app.route("/deal")
def deal():
    """Given pawn price (cents) + market prices, return deal ratings."""
    pawn_cents  = request.args.get("pawn", type=int)
    loose_cents = request.args.get("loose", type=int)
    cib_cents   = request.args.get("cib", type=int)
    new_cents   = request.args.get("new", type=int)

    if not pawn_cents:
        return jsonify({})

    out = {}
    for label, market in [("loose", loose_cents), ("cib", cib_cents), ("new", new_cents)]:
        r = scraper.deal_rating(pawn_cents, market)
        if r:
            out[label] = r

    return jsonify(out)


@app.route("/scan", methods=["POST"])
def scan():
    """
    Accepts an image upload. Sends it to Claude for game ID + condition analysis.
    Returns JSON with identified game, console, condition, and optional follow-up
    photo request.
    """
    api_key = _get_anthropic_key()
    if not api_key:
        return jsonify({"error": "Anthropic API key not configured"}), 500

    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400

    img_file = request.files["image"]
    img_bytes = img_file.read()
    if not img_bytes:
        return jsonify({"error": "Empty image"}), 400

    mime = img_file.mimetype or "image/jpeg"
    b64  = base64.standard_b64encode(img_bytes).decode("utf-8")

    # Build the console list for the prompt
    console_options = ", ".join(f'"{k}"' for k in scraper.CONSOLES.keys())

    system_prompt = (
        "You are a retro video game condition expert helping a reseller evaluate games at pawn shops. "
        "Your job is to identify the game and assess its physical condition from photos. "
        "Be accurate — the user is making a buy decision. "
        "If you cannot confidently identify the game or assess condition, ask for a specific additional photo."
    )

    user_prompt = f"""Analyze this image of a video game or console.

Respond ONLY with a valid JSON object using this exact schema:
{{
  "identified": true or false,
  "game_name": "exact game title or null",
  "console_key": one of [{console_options}] or null,
  "console_display": "human-readable console name or null",
  "condition": "loose" | "cib" | "cib_incomplete" | "new_sealed" | "damaged" | "unknown",
  "condition_grade": "Excellent" | "Good" | "Fair" | "Poor" | null,
  "condition_notes": "brief description of physical condition — label wear, case cracks, yellowing, etc.",
  "confidence": "high" | "medium" | "low",
  "needs_more_photos": true or false,
  "photo_request": "specific instruction for what photo to take next, or null",
  "resale_notes": "1-2 sentences on sellability, common vs rare, demand level"
}}

Condition definitions:
- loose: cartridge or disc only, no box or manual
- cib: complete in box (has original box + manual + game)
- cib_incomplete: has box but missing manual, or vice versa
- new_sealed: factory sealed
- damaged: significant physical damage affecting value
- unknown: cannot determine from this photo"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 512,
                "system": system_prompt,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                        {"type": "text",  "text": user_prompt},
                    ],
                }],
            },
            verify=certifi.where(),
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as e:
        return jsonify({"error": f"Claude API error: {e}"}), 502

    raw_text = resp.json()["content"][0]["text"].strip()

    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        return jsonify({"error": "Unexpected response from Claude", "raw": raw_text}), 502

    return jsonify(result)


@app.route("/scan/followup", methods=["POST"])
def scan_followup():
    """
    Second (or third) image in a multi-photo condition assessment.
    Accepts the new image + context from previous scan result.
    """
    api_key = _get_anthropic_key()
    if not api_key:
        return jsonify({"error": "Anthropic API key not configured"}), 500

    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400

    img_file  = request.files["image"]
    img_bytes = img_file.read()
    mime      = img_file.mimetype or "image/jpeg"
    b64       = base64.standard_b64encode(img_bytes).decode("utf-8")

    prev_context = request.form.get("context", "{}")
    try:
        prev = json.loads(prev_context)
    except Exception:
        prev = {}

    game_name = prev.get("game_name", "unknown game")
    console   = prev.get("console_display", "unknown console")
    prev_cond = prev.get("condition", "unknown")
    photo_req = prev.get("photo_request", "additional view")

    console_options = ", ".join(f'"{k}"' for k in scraper.CONSOLES.keys())

    user_prompt = f"""This is a follow-up photo for: {game_name} ({console}).

Previous assessment: condition={prev_cond}, you requested: "{photo_req}"

Based on this additional photo, provide a final assessment. Respond ONLY with valid JSON:
{{
  "identified": true or false,
  "game_name": "{game_name}",
  "console_key": one of [{console_options}] or null,
  "console_display": "{console}",
  "condition": "loose" | "cib" | "cib_incomplete" | "new_sealed" | "damaged" | "unknown",
  "condition_grade": "Excellent" | "Good" | "Fair" | "Poor" | null,
  "condition_notes": "updated condition description incorporating both photos",
  "confidence": "high" | "medium" | "low",
  "needs_more_photos": true or false,
  "photo_request": "specific next photo request or null",
  "resale_notes": "1-2 sentences on sellability"
}}"""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 512,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                        {"type": "text",  "text": user_prompt},
                    ],
                }],
            },
            verify=certifi.where(),
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as e:
        return jsonify({"error": f"Claude API error: {e}"}), 502

    raw_text = resp.json()["content"][0]["text"].strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        return jsonify({"error": "Unexpected response from Claude", "raw": raw_text}), 502

    return jsonify(result)


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    db.init()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
