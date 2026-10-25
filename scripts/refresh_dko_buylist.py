"""
Refresh static/dko_buylist.json from DK Oldies' sell page.

DK Oldies blocks most server/datacenter IPs, so run this from your own
computer (home connection), then commit the JSON:

    python scripts/refresh_dko_buylist.py
    git add static/dko_buylist.json && git commit -m "Refresh DKO buylist"

If their page blocks plain requests even from home, save the page from your
browser (Ctrl+S, "Webpage, HTML only") and pass the file instead:

    python scripts/refresh_dko_buylist.py --html ~/Downloads/sell-video-games.html
"""
import argparse, json, os, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import scraper  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static", "dko_buylist.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", help="parse a saved copy of the sell page instead of fetching it")
    args = ap.parse_args()

    if args.html:
        with open(args.html, encoding="utf-8", errors="ignore") as f:
            html = f.read()
    else:
        resp = scraper.session.get("https://www.dkoldies.com/sell-video-games/", timeout=30)
        if not resp.ok or "just a moment" in resp.text.lower():
            sys.exit(f"Blocked (status {resp.status_code}). Save the page from your browser and use --html.")
        html = resp.text

    items = scraper.parse_dko_sell_page(html)
    if not items:
        sys.exit("No buylist rows found — the page layout may have changed (look for .pd_row elements).")

    payload = {"source": "https://www.dkoldies.com/sell-video-games/",
               "fetched_at": int(time.time()), "items": items}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=0, ensure_ascii=False)
    print(f"Wrote {len(items)} items to {os.path.normpath(OUT)}")


if __name__ == "__main__":
    main()
