"""
Scrapers for PriceCharting (market prices) and DK Oldies (retail + buy prices).
"""
import os, re, json, time, unicodedata
import requests
from bs4 import BeautifulSoup
import certifi

session = requests.Session()
session.verify = certifi.where()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.5",
})

# DK Oldies SearchSpring site ID (found in their page JS)
DKO_SITE_ID = "6pjfbh"

CONSOLES = {
    "nes":       {"name": "NES",             "pc": "nes"},
    "snes":      {"name": "SNES",            "pc": "super-nintendo"},
    "n64":       {"name": "N64",             "pc": "nintendo-64"},
    "gameboy":   {"name": "Game Boy",        "pc": "gameboy"},
    "gbc":       {"name": "Game Boy Color",  "pc": "gameboy-color"},
    "gba":       {"name": "GBA",             "pc": "gameboy-advance"},
    "gamecube":  {"name": "GameCube",        "pc": "gamecube"},
    "wii":       {"name": "Wii",             "pc": "wii"},
    "nds":       {"name": "Nintendo DS",     "pc": "nintendo-ds"},
    "3ds":       {"name": "3DS",             "pc": "nintendo-3ds"},
    "switch":    {"name": "Switch",          "pc": "nintendo-switch"},
    "genesis":   {"name": "Sega Genesis",    "pc": "sega-genesis"},
    "dreamcast": {"name": "Dreamcast",       "pc": "sega-dreamcast"},
    "saturn":    {"name": "Sega Saturn",     "pc": "sega-saturn"},
    "gamegear":  {"name": "Game Gear",       "pc": "sega-game-gear"},
    "ps1":       {"name": "PS1",             "pc": "playstation"},
    "ps2":       {"name": "PS2",             "pc": "playstation-2"},
    "ps3":       {"name": "PS3",             "pc": "playstation-3"},
    "psp":       {"name": "PSP",             "pc": "psp"},
    "xbox":      {"name": "Xbox",            "pc": "xbox"},
    "xbox360":   {"name": "Xbox 360",        "pc": "xbox-360"},
    "atari2600": {"name": "Atari 2600",      "pc": "atari-2600"},
}


def search_pricecharting(query, console_key=None):
    """Search PriceCharting and return list of matching games (up to 15)."""
    try:
        resp = session.get(
            "https://www.pricecharting.com/search-products",
            params={"q": query, "type": "videogames"},
            timeout=12,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"[scraper] search request failed: {e}", flush=True)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find(id="games_table") or soup.find("table", class_="games")
    if not table:
        print(f"[scraper] no table found; status={resp.status_code} url={resp.url}", flush=True)
        return []

    pc_console_filter = CONSOLES.get(console_key, {}).get("pc") if console_key else None
    results = []

    for row in table.select("tbody tr"):
        link_el = row.select_one("td.title a")
        console_el = row.select_one("td.console")
        if not link_el:
            continue

        href = link_el.get("href", "")
        if "/game/" not in href:
            continue

        game_path = href.split("/game/", 1)[1]  # "nintendo-64/zelda-ocarina-of-time"
        parts = game_path.split("/")
        if len(parts) < 2:
            continue

        pc_console = parts[0]
        game_slug = parts[1]

        if pc_console_filter and pc_console != pc_console_filter:
            continue

        console_name = console_el.text.strip() if console_el else pc_console.replace("-", " ").title()

        price_els = row.select("td.price")
        loose = _parse_price(price_els[0].text) if len(price_els) > 0 else None
        cib   = _parse_price(price_els[1].text) if len(price_els) > 1 else None

        results.append({
            "name":         link_el.text.strip(),
            "console_name": console_name,
            "pc_console":   pc_console,
            "slug":         game_slug,
            "loose_cents":  loose,
            "cib_cents":    cib,
        })

    return results[:15]


def get_pricecharting_prices(pc_console, game_slug):
    """Fetch full price breakdown from a PriceCharting game page."""
    url = f"https://www.pricecharting.com/game/{pc_console}/{game_slug}"
    try:
        resp = session.get(url, timeout=12)
        resp.raise_for_status()
    except Exception as e:
        return {"title": game_slug.replace("-", " ").title(), "url": url, "prices": {}, "error": str(e)}

    prices = {}

    # Primary: parse VGPC.chart_data JS variable (most recent price point per condition)
    chart_match = re.search(r"VGPC\.chart_data\s*=\s*(\{.*?\});", resp.text, re.DOTALL)
    if chart_match:
        try:
            chart_data = json.loads(chart_match.group(1))
            for key, label in [("used", "loose"), ("cib", "cib"), ("new", "new"),
                                ("graded", "graded"), ("boxonly", "box_only"),
                                ("manualonly", "manual_only")]:
                if key in chart_data and chart_data[key]:
                    prices[label] = chart_data[key][-1][1]  # cents
        except (json.JSONDecodeError, KeyError, IndexError):
            pass

    # Fallback: parse price table HTML
    if not prices:
        soup = BeautifulSoup(resp.text, "html.parser")
        for price_id, label in [("used-price", "loose"), ("complete-price", "cib"), ("new-price", "new")]:
            el = soup.find(id=price_id)
            if el:
                try:
                    raw = el.get("data-price") or el.text.strip().replace("$", "")
                    prices[label] = int(float(raw) * 100)
                except (ValueError, AttributeError):
                    pass

    # Title
    soup = BeautifulSoup(resp.text, "html.parser")
    title_el = soup.find("h1")
    if title_el:
        # Get first non-empty text node (avoids nested console-name spans)
        title = next((t.strip() for t in title_el.strings if t.strip()), "")
        if not title:
            title = " ".join(title_el.get_text().split())
        # PriceCharting h1 sometimes reads "Game Title Prices" — strip the suffix
        if title.endswith(" Prices"):
            title = title[: -len(" Prices")]
    else:
        title = game_slug.replace("-", " ").title()

    return {"title": title, "url": url, "prices": prices}


def get_dkoldies_price(game_name, console_name=""):
    """Look up a game on DK Oldies via their SearchSpring API."""
    query = f"{game_name} {console_name}".strip()
    try:
        resp = session.get(
            f"https://{DKO_SITE_ID}.a.searchspring.io/api/search/search.json",
            params={
                "siteId": DKO_SITE_ID,
                "q": query,
                "resultsFormat": "json",
                "resultsPerPage": "5",
            },
            timeout=8,
        )
        if not resp.ok:
            return None

        data = resp.json()
        results = data.get("results", [])
        if not results:
            return None

        first = results[0]
        price_raw = (first.get("price") or first.get("ss_sale_price")
                     or first.get("msrp") or first.get("ss_price"))
        if not price_raw:
            return None

        price_cents = int(float(str(price_raw).replace("$", "").replace(",", "")) * 100)
        return {
            "name":        first.get("name", ""),
            "price_cents": price_cents,
            "url":         first.get("url", ""),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# DK Oldies buy-list (what they pay sellers)
# ---------------------------------------------------------------------------
_dko_buylist_cache = {"data": {}, "fetched_at": 0}
_DKO_BUYLIST_TTL   = 3600  # 1 hour


_DKO_BUYLIST_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "dko_buylist.json")


def _load_buylist_from_json():
    """Load the bundled DKO buylist JSON (scraped locally, committed to repo)."""
    try:
        with open(_DKO_BUYLIST_JSON, encoding="utf-8") as f:
            items = json.load(f)
        return {_normalise(item["name"]): {"name": item["name"], "cents": item["cents"]} for item in items}
    except Exception as e:
        print(f"[scraper] dko buylist json load failed: {e}", flush=True)
        return {}


def _fetch_dkoldies_buylist():
    """Try to scrape fresh DKO buy prices; fall back to bundled JSON if blocked."""
    try:
        resp = session.get("https://www.dkoldies.com/sell-video-games/", timeout=20)
        if not resp.ok or "just a moment" in resp.text.lower():
            raise ValueError(f"Blocked or bad status: {resp.status_code}")
        soup = BeautifulSoup(resp.text, "html.parser")
        buylist = {}
        for row in soup.select(".pd_row"):
            label_el = row.select_one(".pd_label") or row.select_one("label")
            price_el = row.select_one(".pd_price")
            if not label_el or not price_el:
                continue
            name  = label_el.get_text(" ", strip=True)
            price_text = re.sub(r"[▲▼]", "", price_el.get_text(strip=True))
            cents = _parse_price(price_text)
            if cents and cents > 0:
                buylist[_normalise(name)] = {"name": name, "cents": cents}
        if buylist:
            return buylist
    except Exception as e:
        print(f"[scraper] live dko buylist fetch failed ({e}), using bundled JSON", flush=True)
    return _load_buylist_from_json()


def _normalise(text):
    """Lowercase, strip punctuation/articles for fuzzy matching."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    # Remove common filler words
    text = re.sub(r"\b(the|a|an|for|in|of|and|with|w|wii|nes|n64|snes|gba|gbc|nds|psp|ps1|ps2|ps3|xbox|gamecube|genesis|saturn|dreamcast)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def get_dkoldies_buy_price(game_name):
    """Return what DK Oldies will pay for a game (from their sell page), or None."""
    global _dko_buylist_cache
    now = time.time()
    if now - _dko_buylist_cache["fetched_at"] > _DKO_BUYLIST_TTL or not _dko_buylist_cache["data"]:
        _dko_buylist_cache["data"]       = _fetch_dkoldies_buylist()
        _dko_buylist_cache["fetched_at"] = now

    buylist = _dko_buylist_cache["data"]
    if not buylist:
        return None

    needle = _normalise(game_name)
    needle_words = set(needle.split())
    if not needle_words:
        return None

    best_key, best_score = None, 0
    for key in buylist:
        key_words = set(key.split())
        if not key_words:
            continue
        overlap = len(needle_words & key_words)
        # Score = fraction of needle words matched, boosted when key is also short
        score = overlap / max(len(needle_words), len(key_words))
        if score > best_score:
            best_score, best_key = score, key

    # Require at least 50% word overlap to count as a match
    if best_score >= 0.5 and best_key:
        entry = buylist[best_key]
        return {"name": entry["name"], "buy_cents": entry["cents"]}
    return None


def deal_rating(pawn_cents, market_cents):
    """Return rating dict for a given pawn price vs market price."""
    if not pawn_cents or not market_cents or market_cents == 0:
        return None
    ratio  = pawn_cents / market_cents
    profit = market_cents - pawn_cents
    margin = round((profit / market_cents) * 100, 1)
    if ratio < 0.40:
        tag, emoji, label = "steal", "🔥", "STEAL"
    elif ratio < 0.65:
        tag, emoji, label = "good",  "✅", "GOOD DEAL"
    elif ratio < 0.85:
        tag, emoji, label = "fair",  "⚠️", "FAIR"
    else:
        tag, emoji, label = "pass",  "❌", "PASS"
    return {
        "tag":        tag,
        "emoji":      emoji,
        "label":      label,
        "profit":     profit,
        "margin_pct": margin,
    }


def _parse_price(text):
    if not text:
        return None
    try:
        return int(float(text.strip().replace("$", "").replace(",", "")) * 100)
    except ValueError:
        return None
