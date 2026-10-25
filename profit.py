"""
Deal math: net proceeds per sales channel, profit/ROI at an asking price,
and the most you should pay to hit your target return.

All money is integer cents. Fee defaults reflect US rates as of Aug 2026
(eBay: 13.6% + $0.30/$0.40 per order for Video Games without a Store;
Mercari: 10% selling fee + ~2.9% + $0.30 processing). Every value can be
overridden by the user's settings, since fees change and vary by account.
"""

DEFAULT_SETTINGS = {
    "ebay_fee_pct":        13.6,
    "ebay_order_fee_low":  30,     # orders <= $10
    "ebay_order_fee_high": 40,     # orders  > $10
    "mercari_fee_pct":     10.0,
    "mercari_proc_pct":    2.9,
    "mercari_proc_flat":   30,
    "ship_cart_cents":     500,    # loose cart / small item
    "ship_boxed_cents":    750,    # boxed game or disc case
    "supplies_cents":      50,     # mailer, sleeve, case
    "buylist_ship_cents":  100,    # your share of a bulk shipment to a buylist
    "target_roi_pct":      50.0,   # minimum return on what you pay
    "min_profit_cents":    500,    # never bother for less than this
}

# How far below the guide price to expect for a given grade. PriceCharting
# values assume an average used copy, so "Good" is treated as typical.
GRADE_FACTORS = {"Excellent": 1.05, "Good": 1.0, "Fair": 0.8, "Poor": 0.55}


def merge_settings(overrides):
    s = dict(DEFAULT_SETTINGS)
    for k, v in (overrides or {}).items():
        if k in s and v not in (None, ""):
            try:
                s[k] = float(v)
            except (TypeError, ValueError):
                pass
    return s


def adjusted_value(market_cents, grade=None):
    if not market_cents:
        return None
    return int(round(market_cents * GRADE_FACTORS.get(grade or "Good", 1.0)))


def channel_net(channel, sale_cents, s, boxed=False):
    """Money you keep from a sale before subtracting what you paid."""
    if not sale_cents:
        return None
    if channel == "buylist":
        return int(sale_cents - s["buylist_ship_cents"])
    ship = s["ship_boxed_cents"] if boxed else s["ship_cart_cents"]
    if channel == "ebay":
        order_fee = s["ebay_order_fee_low"] if sale_cents <= 1000 else s["ebay_order_fee_high"]
        fees = sale_cents * s["ebay_fee_pct"] / 100 + order_fee
    elif channel == "mercari":
        fees = sale_cents * (s["mercari_fee_pct"] + s["mercari_proc_pct"]) / 100 + s["mercari_proc_flat"]
    else:
        raise ValueError(f"unknown channel {channel}")
    return int(round(sale_cents - fees - ship - s["supplies_cents"]))


def max_offer(net_cents, s):
    """Highest purchase price that still meets both target ROI and minimum profit."""
    if net_cents is None or net_cents <= 0:
        return 0
    by_roi = net_cents / (1 + s["target_roi_pct"] / 100)
    by_min = net_cents - s["min_profit_cents"]
    return max(0, int(min(by_roi, by_min)))


def rate(profit_cents, cost_cents, s):
    if cost_cents <= 0:
        roi = None
    else:
        roi = profit_cents / cost_cents * 100
    if profit_cents < s["min_profit_cents"] or (roi is not None and roi < 20):
        return "pass"
    if roi is None or roi >= 100:
        return "steal"
    if roi >= s["target_roi_pct"]:
        return "good"
    return "fair"


LABELS = {"steal": "STEAL", "good": "GOOD DEAL", "fair": "THIN", "pass": "PASS"}


def evaluate(market, asking_cents=None, buylist_cents=None, grade=None, settings=None, conditions=None):
    """
    market: {"loose": cents, "cib": cents, "new": cents}
    conditions: which of those the user actually has (default: all). The buylist
    exit is included whenever a buylist price is given.
    Returns per-condition, per-channel breakdown plus the single best exit.
    """
    s = merge_settings(settings)
    rows = []
    for cond in (conditions or ("loose", "cib", "new")):
        if cond not in ("loose", "cib", "new"):
            continue
        value = adjusted_value(market.get(cond), grade)
        if not value:
            continue
        boxed = cond != "loose"
        for channel in ("ebay", "mercari"):
            net = channel_net(channel, value, s, boxed=boxed)
            rows.append(_row(cond, channel, value, net, asking_cents, s))
    if buylist_cents:
        net = channel_net("buylist", buylist_cents, s)
        rows.append(_row("buylist", "buylist", buylist_cents, net, asking_cents, s))

    best = max(rows, key=lambda r: r["net_cents"], default=None)
    instant_flip = bool(buylist_cents and asking_cents is not None
                        and channel_net("buylist", buylist_cents, s) > asking_cents)
    return {"rows": rows, "best": best, "instant_flip": instant_flip, "settings": s}


def _row(cond, channel, sale, net, asking, s):
    row = {"condition": cond, "channel": channel, "sale_cents": sale, "net_cents": net,
           "costs_cents": sale - net,  # fees + shipping + supplies
           "max_offer_cents": max_offer(net, s)}
    if asking is not None:
        profit = net - asking
        row.update(profit_cents=profit,
                   roi_pct=round(profit / asking * 100, 1) if asking > 0 else None,
                   rating=rate(profit, asking, s))
        row["label"] = LABELS[row["rating"]]
    return row
