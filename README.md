# Retro Pricer

Sourcing calculator for retro game resellers: look up a game (or photograph it, or a whole lot),
enter the asking price, and see profit after fees on each exit — eBay, Mercari, or the DK Oldies
buylist — plus the most you should pay. Purchases and sales go in a per-device inventory.

## Running locally

    pip install -r requirements.txt
    ANTHROPIC_API_KEY=... python app.py      # http://localhost:5000
    python -m pytest -q tests

## Configuration (environment variables)

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Photo scanning (single game and lots) |
| `CLAUDE_MODEL` | Vision model, default `claude-haiku-4-5` |
| `PRICECHARTING_TOKEN` | Use the official PriceCharting API (recommended) |
| `PRICECHARTING_SCRAPE` | `1` (default) scrapes pricecharting.com when no token is set; set `0` in production |
| `DB_PATH` | SQLite location; point at a persistent disk or inventory is lost on redeploy |
| `PRICE_CACHE_TTL` | Seconds to cache a game's prices (default 6 h) |

## Data sources and licensing — read before charging for the app

* **PriceCharting.** Their API terms license data for internal use only; showing it inside an app
  other people use needs a commercial license and written permission. Scraping their site is no
  better. Get a commercial agreement before selling the app, or replace this source.
* **DK Oldies buylist.** Their sell page blocks most server IPs. The app falls back to
  `static/dko_buylist.json`; refresh it from a home connection with
  `python scripts/refresh_dko_buylist.py` and commit the result. Check their terms too.
* **eBay.** Local pickup listings are scraped from the public search page (fragile). Sold-price
  data is only linked out to (`LH_Sold=1` search), since eBay's sold-data API is closed to small developers.

## Hosting

`render.yaml` uses a paid Starter instance with a 1 GB disk at `/var/data` so inventory survives
redeploys and there are no free-tier cold starts. `/health` reports which price source and buylist
are active.
