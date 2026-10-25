import os, sys, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")

import scraper, profit, repro


def test_parse_price():
    assert scraper._parse_price("$1,234.56") == 123456
    assert scraper._parse_price("$5.00 to $9.00") == 500
    assert scraper._parse_price("N/A") is None


def test_buylist_matching_is_console_aware():
    bl = scraper._index_buylist([
        {"name": "Super Mario 64 - N64", "cents": 2000},
        {"name": "Mario Kart 64 - N64", "cents": 1500},
        {"name": "Mario Kart Double Dash - GameCube", "cents": 1200},
        {"name": "Tetris - Game Boy", "cents": 300},
    ])
    assert scraper.match_buylist(bl, "Mario Kart 64", "n64")["cents"] == 1500
    assert scraper.match_buylist(bl, "Super Mario 64", "n64")["cents"] == 2000
    # Different console must not match
    assert scraper.match_buylist(bl, "Tetris", "nes") is None
    # Weak overlap must not match
    assert scraper.match_buylist(bl, "Mario Party", "n64") is None


def test_buylist_negative_cache(monkeypatch):
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ValueError("blocked")
    monkeypatch.setattr(scraper, "_fetch_live_buylist", boom)
    monkeypatch.setattr(scraper, "_load_buylist_from_json", lambda: {})
    scraper._dko_buylist_cache.update(data={}, fetched_at=0.0, source="none")
    for _ in range(20):
        scraper.get_dkoldies_buy_price("Super Mario 64", "n64")
    assert calls["n"] == 1


def test_ebay_parse_new_and_old_layouts():
    html = """
    <ul>
      <li class="s-card"><a class="su-link" href="https://www.ebay.com/itm/123?x=1">
        <div class="s-card__title"><span>Zelda Ocarina N64</span> Opens in a new window or tab</div></a>
        <span class="s-card__price">$24.99</span><div class="s-card__subtitle">Pre-Owned</div></li>
      <li class="s-item"><a class="s-item__link" href="https://www.ebay.com/itm/456"></a>
        <div class="s-item__title">Shop on eBay</div><span class="s-item__price">$1.00</span></li>
      <li class="s-item"><a class="s-item__link" href="https://www.ebay.com/itm/789?y"></a>
        <div class="s-item__title">NES lot</div><span class="s-item__price">$40.00 to $60.00</span></li>
    </ul>"""
    items = scraper.parse_ebay_results(html)
    assert [i["title"] for i in items] == ["Zelda Ocarina N64", "NES lot"]
    assert items[0]["url"] == "https://www.ebay.com/itm/123"
    assert items[1]["price_cents"] == 4000


def test_profit_math():
    s = profit.merge_settings({})
    # $30 loose on eBay: 13.6% (4.08) + 0.40 + 5.00 ship + 0.50 supplies = 9.98 -> net 20.02
    assert profit.channel_net("ebay", 3000, s) == 2002
    # $8 sale uses the low per-order fee
    assert profit.channel_net("ebay", 800, s) == 800 - round(800 * 0.136 + 30 + 500 + 50)
    # max offer meets 50% ROI and $5 min profit
    mo = profit.max_offer(2002, s)
    assert mo == 1334 and 2002 - mo >= 500
    ev = profit.evaluate({"loose": 3000}, asking_cents=1000, buylist_cents=1500)
    assert ev["instant_flip"] is True
    assert ev["best"]["channel"] == "mercari"  # 12.9%+0.30 beats 13.6%+0.40 at equal price
    assert {r["rating"] for r in ev["rows"]} <= {"steal", "good", "fair", "pass"}


def test_repro():
    assert repro.assess("Pokemon Crystal", "gbc", "cart")["level"] == "high"
    assert repro.assess("Tetris", "gameboy", "cart", 800)["level"] is None
    assert repro.assess("Some Rare Game", "snes", "cart", 9000)["level"] == "check"
    assert repro.assess("Pokemon Colosseum", "gamecube", "disc")["level"] is None


def test_inventory_and_endpoints(monkeypatch):
    import app as appmod
    c = appmod.app.test_client()
    h = {"X-Device-Id": "abcdefghijklmnop1234"}
    assert c.get("/api/inventory").status_code == 400
    r = c.post("/api/inventory", json={"title": "Mario Kart 64", "paid_cents": 1000}, headers=h)
    assert r.status_code == 201
    iid = r.get_json()["id"]
    c.patch(f"/api/inventory/{iid}", json={"sold_cents": 3000, "fees_cents": 450, "ship_cents": 500}, headers=h)
    data = c.get("/api/inventory", headers=h).get_json()
    assert data["summary"]["profit_cents"] == 3000 - 1000 - 450 - 500
    # other devices can't see it
    assert c.get("/api/inventory", headers={"X-Device-Id": "zzzzzzzzzzzzzzzz9999"}).get_json()["items"] == []
    assert c.get("/api/inventory.csv?device=abcdefghijklmnop1234").status_code == 200
    assert c.post("/api/evaluate", json={"prices": {"loose": 3000}, "asking_cents": 500}).status_code == 200
    assert c.get("/").status_code == 200
    assert c.get("/sw.js").status_code == 200
    assert c.get("/health").get_json()["ok"] is True
