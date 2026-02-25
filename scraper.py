"""
Scrapers for PriceCharting (market prices) and DK Oldies (retail prices).
"""
import re, json, time
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
