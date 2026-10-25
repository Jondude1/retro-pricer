"""
Price sources:
  * PriceCharting market values: official API when PRICECHARTING_TOKEN is set,
    otherwise a page scrape (personal use only; see README for licensing).
  * DK Oldies retail price (SearchSpring) and buylist (what they pay sellers).
  * eBay local-pickup listings (public search page scrape).
"""
import os, re, json, time, threading, unicodedata
import requests
from bs4 import BeautifulSoup
import certifi

session = requests.Session()
session.verify = certifi.where()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.5",
})

PRICECHARTING_TOKEN = os.environ.get("PRICECHARTING_TOKEN", "").strip()
# Scraping PriceCharting is only appropriate for personal use. Set to 0 in
# production once you have an API token / commercial license.
PRICECHARTING_SCRAPE = os.environ.get("PRICECHARTING_SCRAPE", "1") != "0"

# DK Oldies SearchSpring site ID (found in their page JS)
DKO_SITE_ID = "6pjfbh"

CONSOLES = {
    "nes":       {"name": "NES",             "pc": "nes",              "media": "cart"},
    "snes":      {"name": "SNES",            "pc": "super-nintendo",   "media": "cart"},
    "n64":       {"name": "N64",             "pc": "nintendo-64",      "media": "cart"},
    "gameboy":   {"name": "Game Boy",        "pc": "gameboy",          "media": "cart"},
    "gbc":       {"name": "Game Boy Color",  "pc": "gameboy-color",    "media": "cart"},
    "gba":       {"name": "GBA",             "pc": "gameboy-advance",  "media": "cart"},
    "gamecube":  {"name": "GameCube",        "pc": "gamecube",         "media": "disc"},
    "wii":       {"name": "Wii",             "pc": "wii",              "media": "disc"},
    "nds":       {"name": "Nintendo DS",     "pc": "nintendo-ds",      "media": "cart"},
    "3ds":       {"name": "3DS",             "pc": "nintendo-3ds",     "media": "cart"},
    "switch":    {"name": "Switch",          "pc": "nintendo-switch",  "media": "cart"},
    "genesis":   {"name": "Sega Genesis",    "pc": "sega-genesis",     "media": "cart"},
    "dreamcast": {"name": "Dreamcast",       "pc": "sega-dreamcast",   "media": "disc"},
    "saturn":    {"name": "Sega Saturn",     "pc": "sega-saturn",      "media": "disc"},
    "gamegear":  {"name": "Game Gear",       "pc": "sega-game-gear",   "media": "cart"},
    "ps1":       {"name": "PS1",             "pc": "playstation",      "media": "disc"},
    "ps2":       {"name": "PS2",             "pc": "playstation-2",    "media": "disc"},
    "ps3":       {"name": "PS3",             "pc": "playstation-3",    "media": "disc"},
    "psp":       {"name": "PSP",             "pc": "psp",              "media": "disc"},
    "xbox":      {"name": "Xbox",            "pc": "xbox",             "media": "disc"},
    "xbox360":   {"name": "Xbox 360",        "pc": "xbox-360",         "media": "disc"},
    "atari2600": {"name": "Atari 2600",      "pc": "atari-2600",       "media": "cart"},
}
PC_TO_KEY = {v["pc"]: k for k, v in CONSOLES.items()}


def slugify(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ---------------------------------------------------------------------------
# PriceCharting official API (https://www.pricecharting.com/api-documentation)
# Limited to 1 call/second, so calls are serialised through a lock.
# ---------------------------------------------------------------------------
_pc_api_lock = threading.Lock()
_pc_api_last = [0.0]


def _pc_api(path, **params):
    with _pc_api_lock:
        wait = 1.05 - (time.time() - _pc_api_last[0])
        if wait > 0:
            time.sleep(wait)
        _pc_api_last[0] = time.time()
        resp = session.get(f"https://www.pricecharting.com/api/{path}",
                           params={"t": PRICECHARTING_TOKEN, **params}, timeout=12)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") == "error":
        raise ValueError(data.get("error-message", "PriceCharting API error"))
    return data


def _api_product_to_result(p):
    console_name = p.get("console-name", "")
    name = p.get("product-name", "")
    return {
        "name":         name,
        "console_name": console_name,
        "pc_console":   slugify(console_name),
        "slug":         slugify(name),
        "pc_id":        str(p.get("id", "")),
        "loose_cents":  p.get("loose-price"),
        "cib_cents":    p.get("cib-price"),
    }


def price_source():
    if PRICECHARTING_TOKEN:
        return "api"
    return "scrape" if PRICECHARTING_SCRAPE else "none"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_pricecharting(query, console_key=None, with_buylist=True):
    """Search PriceCharting and return list of matching games (up to 15)."""
    pc_filter = CONSOLES.get(console_key, {}).get("pc") if console_key else None
    if PRICECHARTING_TOKEN:
        results = _search_api(query, pc_filter)
    elif PRICECHARTING_SCRAPE:
        results = _search_scrape(query, pc_filter)
    else:
        results = []

    results = results[:15]
    if with_buylist:
        for r in results:
            ck = PC_TO_KEY.get(r["pc_console"], "")
            dk = get_dkoldies_buy_price(r["name"], ck)
            r["console_key"]      = ck
            r["dk_buy_cents"]     = dk["buy_cents"] if dk else None
            r["dk_buy_condition"] = dk["buy_condition"] if dk else None
    return results


def _search_api(query, pc_filter):
    try:
        q = query
        if pc_filter:
            key = PC_TO_KEY.get(pc_filter)
            q = f"{query} {CONSOLES[key]['name']}" if key else query
        data = _pc_api("products", q=q)
    except Exception as e:
        print(f"[scraper] PriceCharting API search failed: {e}", flush=True)
        return []
    results = [_api_product_to_result(p) for p in data.get("products", [])]
    if pc_filter:
        results = [r for r in results if r["pc_console"] == pc_filter]
    return results


def _search_scrape(query, pc_filter):
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

    # Single exact match redirects straight to the game page
    if "/game/" in resp.url:
        parts = resp.url.split("/game/", 1)[1].split("?")[0].split("/")
        if len(parts) >= 2 and (not pc_filter or parts[0] == pc_filter):
            page = _parse_game_page(resp.text, resp.url, parts[1])
            return [{
                "name": page["title"], "console_name": parts[0].replace("-", " ").title(),
                "pc_console": parts[0], "slug": parts[1], "pc_id": "",
                "loose_cents": page["prices"].get("loose"), "cib_cents": page["prices"].get("cib"),
            }]
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find(id="games_table") or soup.find("table", class_="games")
    if not table:
        print(f"[scraper] no table found; status={resp.status_code} url={resp.url}", flush=True)
        return []

    results = []
    for row in table.select("tbody tr"):
        link_el = row.select_one("td.title a")
        console_el = row.select_one("td.console")
        if not link_el:
            continue
        href = link_el.get("href", "")
        if "/game/" not in href:
            continue
        parts = href.split("/game/", 1)[1].split("/")
        if len(parts) < 2:
            continue
        pc_console, game_slug = parts[0], parts[1]
        if pc_filter and pc_console != pc_filter:
            continue
        price_els = row.select("td.price")
        results.append({
            "name":         link_el.text.strip(),
            "console_name": console_el.text.strip() if console_el else pc_console.replace("-", " ").title(),
            "pc_console":   pc_console,
            "slug":         game_slug,
            "pc_id":        "",
            "loose_cents":  _parse_price(price_els[0].text) if len(price_els) > 0 else None,
            "cib_cents":    _parse_price(price_els[1].text) if len(price_els) > 1 else None,
        })
    return results


# ---------------------------------------------------------------------------
# Full price breakdown
# ---------------------------------------------------------------------------

def get_pricecharting_prices(pc_console, game_slug, pc_id=""):
    """Return {"title", "url", "prices": {loose, cib, new, graded, box_only, manual_only}}."""
    url = f"https://www.pricecharting.com/game/{pc_console}/{game_slug}"
    if PRICECHARTING_TOKEN:
        try:
            p = _pc_api("product", id=pc_id) if pc_id else _pc_api("product", q=f"{game_slug.replace('-', ' ')} {pc_console.replace('-', ' ')}")
            prices = {}
            for key, label in [("loose-price", "loose"), ("cib-price", "cib"), ("new-price", "new"),
                               ("graded-price", "graded"), ("box-only-price", "box_only"),
                               ("manual-only-price", "manual_only")]:
                if p.get(key):
                    prices[label] = p[key]
            return {"title": p.get("product-name", game_slug), "url": url, "prices": prices}
        except Exception as e:
            print(f"[scraper] PriceCharting API product failed: {e}", flush=True)
            return {"title": game_slug.replace("-", " ").title(), "url": url, "prices": {}, "error": str(e)}

    if not PRICECHARTING_SCRAPE:
        return {"title": game_slug.replace("-", " ").title(), "url": url, "prices": {},
                "error": "No price source configured"}
    try:
        resp = session.get(url, timeout=12)
        resp.raise_for_status()
    except Exception as e:
        return {"title": game_slug.replace("-", " ").title(), "url": url, "prices": {}, "error": str(e)}
    return _parse_game_page(resp.text, url, game_slug)


def _parse_game_page(html, url, game_slug):
    prices = {}
    chart_match = re.search(r"VGPC\.chart_data\s*=\s*(\{.*?\});", html, re.DOTALL)
    if chart_match:
        try:
            chart_data = json.loads(chart_match.group(1))
            for key, label in [("used", "loose"), ("cib", "cib"), ("new", "new"),
                               ("graded", "graded"), ("boxonly", "box_only"),
                               ("manualonly", "manual_only")]:
                if chart_data.get(key):
                    prices[label] = chart_data[key][-1][1]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            pass

    soup = BeautifulSoup(html, "html.parser")
    if not prices:
        for price_id, label in [("used_price", "loose"), ("complete_price", "cib"), ("new_price", "new"),
                                ("used-price", "loose"), ("complete-price", "cib"), ("new-price", "new")]:
            el = soup.find(id=price_id)
            if el and label not in prices:
                cents = _parse_price(el.get("data-price") or el.get_text(" ", strip=True))
                if cents:
                    prices[label] = cents

    title_el = soup.find("h1")
    if title_el:
        title = next((t.strip() for t in title_el.strings if t.strip()), "") or " ".join(title_el.get_text().split())
        if title.endswith(" Prices"):
            title = title[: -len(" Prices")]
    else:
        title = game_slug.replace("-", " ").title()
    return {"title": title, "url": url, "prices": prices}


# ---------------------------------------------------------------------------
# DK Oldies retail price (SearchSpring)
# ---------------------------------------------------------------------------

def get_dkoldies_price(game_name, console_name=""):
    query = f"{game_name} {console_name}".strip()
    try:
        resp = session.get(
            f"https://{DKO_SITE_ID}.a.searchspring.io/api/search/search.json",
            params={"siteId": DKO_SITE_ID, "q": query, "resultsFormat": "json", "resultsPerPage": "5"},
            timeout=8,
        )
        if not resp.ok:
            return None
        results = resp.json().get("results", [])
        if not results:
            return None
        first = results[0]
        price_raw = (first.get("price") or first.get("ss_sale_price")
                     or first.get("msrp") or first.get("ss_price"))
        if not price_raw:
            return None
        return {
            "name":        first.get("name", ""),
            "price_cents": int(round(float(str(price_raw).replace("$", "").replace(",", "")) * 100)),
            "url":         first.get("url", ""),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# DK Oldies buylist (what they pay sellers)
# ---------------------------------------------------------------------------
_DKO_BUYLIST_TTL       = 6 * 3600   # successful fetch lifetime
_DKO_BUYLIST_RETRY     = 15 * 60    # wait this long after a failed live fetch
_DKO_BUYLIST_JSON      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "dko_buylist.json")
_dko_lock              = threading.Lock()
_dko_buylist_cache     = {"data": {}, "fetched_at": 0.0, "source": "none"}


def _load_buylist_from_json():
    """Bundled buylist (refresh with scripts/refresh_dko_buylist.py)."""
    try:
        with open(_DKO_BUYLIST_JSON, encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            print("[scraper] bundled dko_buylist.json is empty — run scripts/refresh_dko_buylist.py", flush=True)
            return {}
        payload = json.loads(raw)
        items = payload["items"] if isinstance(payload, dict) else payload
        return _index_buylist(items)
    except Exception as e:
        print(f"[scraper] dko buylist json load failed: {e}", flush=True)
        return {}


def _index_buylist(items):
    out = {}
    for item in items:
        name, cents = item.get("name"), item.get("cents")
        if not name or not cents:
            continue
        out[name] = {"name": name, "cents": int(cents), "console": item.get("console", ""),
                     "words": set(_title_words(name))}
    return out


def parse_dko_sell_page(html):
    """Parse DK Oldies' sell page HTML into [{name, cents, console}]."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for row in soup.select(".pd_row"):
        label_el = row.select_one(".pd_label") or row.select_one("label")
        price_el = row.select_one(".pd_price")
        if not label_el or not price_el:
            continue
        name = label_el.get_text(" ", strip=True)
        cents = _parse_price(re.sub(r"[▲▼]", "", price_el.get_text(strip=True)))
        console = row.get("data-console") or row.get("data-platform") or ""
        if cents and cents > 0:
            items.append({"name": name, "cents": cents, "console": console})
    return items


def _fetch_live_buylist():
    resp = session.get("https://www.dkoldies.com/sell-video-games/", timeout=15)
    if not resp.ok or "just a moment" in resp.text.lower():
        raise ValueError(f"blocked or bad status: {resp.status_code}")
    items = parse_dko_sell_page(resp.text)
    if not items:
        raise ValueError("no buylist rows found (page layout may have changed)")
    return _index_buylist(items)


def _get_buylist():
    """Return the buylist, refreshing at most once per TTL (or retry window on failure)."""
    now = time.time()
    c = _dko_buylist_cache
    ttl = _DKO_BUYLIST_TTL if c["source"] == "live" else _DKO_BUYLIST_RETRY
    if c["fetched_at"] and now - c["fetched_at"] < ttl:
        return c["data"]
    with _dko_lock:
        if c["fetched_at"] and time.time() - c["fetched_at"] < ttl:
            return c["data"]
        try:
            data, source = _fetch_live_buylist(), "live"
        except Exception as e:
            print(f"[scraper] live dko buylist fetch failed ({e}); using bundled JSON", flush=True)
            data = _load_buylist_from_json()
            source = "bundled" if data else "none"
            if not data and c["data"]:
                data, source = c["data"], c["source"]  # keep last good copy
        c.update(data=data, fetched_at=time.time(), source=source)
        return data


def buylist_status():
    c = _dko_buylist_cache
    return {"source": c["source"], "items": len(c["data"]),
            "age_seconds": int(time.time() - c["fetched_at"]) if c["fetched_at"] else None}


_FILLER = {"the", "a", "an", "for", "in", "of", "and", "with", "w", "game", "video",
           "cart", "cartridge", "only", "complete", "cib", "loose", "disc"}
_CONSOLE_WORDS = {"nes", "snes", "n64", "gba", "gbc", "gb", "nds", "ds", "3ds", "psp", "ps1", "ps2", "ps3",
                  "psx", "playstation", "xbox", "360", "gamecube", "gcn", "genesis", "saturn", "dreamcast",
                  "wii", "switch", "nintendo", "sega", "super", "boy", "color", "advance", "atari", "2600",
                  "gear", "64"}


def _title_words(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    words = re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()
    return [w for w in words if w not in _FILLER]


def _console_hint_matches(entry, console_key):
    """True/False when the buylist entry clearly names a console; None if it doesn't say."""
    if not console_key:
        return None
    text = (entry.get("console", "") + " " + entry["name"]).lower()
    aliases = {
        "nes": [r"\bnes\b", r"\bnintendo entertainment"], "snes": [r"\bsnes\b", r"super nintendo"],
        "n64": [r"\bn64\b", r"nintendo 64"], "gameboy": [r"game ?boy(?! (color|advance))", r"\bgb\b"],
        "gbc": [r"\bgbc\b", r"game ?boy color"], "gba": [r"\bgba\b", r"game ?boy advance"],
        "gamecube": [r"game ?cube", r"\bgcn\b"], "wii": [r"\bwii\b(?! ?u)"], "nds": [r"\bnds\b", r"nintendo ds", r"(?<!3)\bds\b"],
        "3ds": [r"\b3ds\b"], "switch": [r"\bswitch\b"], "genesis": [r"genesis"], "dreamcast": [r"dreamcast"],
        "saturn": [r"saturn"], "gamegear": [r"game ?gear"], "ps1": [r"\bps1\b", r"\bpsx\b", r"playstation 1\b", r"playstation(?! ?[2-5])"],
        "ps2": [r"\bps2\b", r"playstation 2"], "ps3": [r"\bps3\b", r"playstation 3"], "psp": [r"\bpsp\b"],
        "xbox": [r"\bxbox\b(?! ?(360|one))"], "xbox360": [r"xbox ?360"], "atari2600": [r"atari", r"\b2600\b"],
    }
    hits = {k for k, pats in aliases.items() if any(re.search(p, text) for p in pats)}
    if not hits:
        return None
    return console_key in hits


# Consoles where DK Oldies lists their buylist price as cart/cartridge only.
DKO_CART_ONLY_CONSOLES = {"nes", "snes", "genesis", "atari2600"}


def dko_buy_condition_label(console_key: str) -> str:
    return "cart only" if console_key in DKO_CART_ONLY_CONSOLES else "complete"


def match_buylist(buylist, game_name, console_key=""):
    """Best buylist entry for a title, preferring entries on the same console."""
    needle = set(w for w in _title_words(game_name) if w not in _CONSOLE_WORDS)
    if not needle:
        return None
    best, best_score = None, 0.0
    for entry in buylist.values():
        key_words = entry["words"] - _CONSOLE_WORDS
        if not key_words:
            continue
        overlap = len(needle & key_words)
        if not overlap:
            continue
        score = overlap / max(len(needle), len(key_words))
        hint = _console_hint_matches(entry, console_key)
        if hint is False:
            continue           # entry is for a different console
        if hint is True:
            score += 0.05      # tie-break toward same console
        if score > best_score:
            best, best_score = entry, score
    # Require a strong match: most of both titles' words must line up
    if best and best_score >= 0.75:
        return best
    return None


def get_dkoldies_buy_price(game_name, console_key=""):
    """Return what DK Oldies will pay for a game, or None."""
    buylist = _get_buylist()
    if not buylist:
        return None
    entry = match_buylist(buylist, game_name, console_key)
    if not entry:
        return None
    return {"name": entry["name"], "buy_cents": entry["cents"],
            "buy_condition": dko_buy_condition_label(console_key)}


# ---------------------------------------------------------------------------
# eBay local pickup (public search page)
# ---------------------------------------------------------------------------

def search_ebay_local(zip_code, radius_miles=25, query="retro video games"):
    """Scrape eBay local-pickup listings within radius of a ZIP code."""
    params = {
        "_nkw":       query,
        "LH_PrefLoc": "99",
        "_sadis":     str(radius_miles),
        "_stpos":     zip_code,
        "_fspt":      "1",
        "LH_BIN":     "1",
        "_sop":       "15",
        "_ipg":       "60",
    }
    try:
        resp = session.get("https://www.ebay.com/sch/i.html", params=params, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        return {"error": f"eBay search failed: {e}", "listings": []}

    listings = parse_ebay_results(resp.text)
    if not listings:
        return {"error": "No local listings found — eBay may have blocked the request or there are no results nearby.",
                "listings": []}
    return {"listings": listings}


def parse_ebay_results(html):
    """Parse eBay search results; handles both the s-card (2025+) and s-item layouts."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("li.s-card, div.s-card, li.s-item, div.s-item")
    out = []
    for item in cards:
        title_el = item.select_one(".s-card__title, .s-item__title")
        price_el = item.select_one(".s-card__price, .s-item__price")
        link_el = item.select_one("a.su-link[href*='/itm/'], a.s-item__link, a[href*='/itm/']")
        if not title_el or not price_el or not link_el:
            continue
        title = title_el.get_text(" ", strip=True)
        title = re.sub(r"\s*Opens in a new window or tab\s*$", "", title, flags=re.I)
        title = re.sub(r"^New Listing\s*", "", title, flags=re.I)
        if not title or title.lower() == "shop on ebay":
            continue
        img_el = item.select_one("img")
        cond_el = item.select_one(".s-card__subtitle, .SECONDARY_INFO, .s-item__subtitle")
        loc_text = ""
        for el in item.select(".s-card__attribute-row, .s-item__location, .s-item__itemLocation"):
            t = el.get_text(" ", strip=True)
            if t.lower().startswith(("from ", "located in", "pickup")) or " mi" in t:
                loc_text = re.sub(r"^(From|Located in)\s*", "", t, flags=re.I)
                break
        out.append({
            "title":       title,
            "price_cents": _parse_price(price_el.get_text(" ", strip=True)) or 0,
            "condition":   cond_el.get_text(" ", strip=True) if cond_el else "",
            "url":         link_el.get("href", "").split("?")[0],
            "image":       (img_el.get("src") or img_el.get("data-src") or "") if img_el else "",
            "location":    loc_text,
            "distance":    None,
        })
    return out


def ebay_sold_url(title, console_name=""):
    """Deep link to eBay sold+completed listings for manual comp checking."""
    from urllib.parse import urlencode
    q = f"{title} {console_name}".strip()
    return "https://www.ebay.com/sch/i.html?" + urlencode({"_nkw": q, "LH_Sold": "1", "LH_Complete": "1", "_sop": "13"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_price(text):
    """'$1,234.56' -> 123456 ; ranges like '$5.00 to $9.00' take the first value."""
    if not text:
        return None
    m = re.search(r"\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", text)
    if not m:
        return None
    try:
        return int(round(float(m.group(1).replace(",", "")) * 100))
    except ValueError:
        return None
