"""
Reproduction / counterfeit risk hints for cartridge games.

This is a checklist, not an authenticator: it flags when a fake would be
costly and tells the user what to look at before paying.
"""
import re

# Titles that are widely counterfeited (mainly Game Boy family and high-value SNES/N64).
HIGH_RISK_PATTERNS = [
    (r"\bpok[eé]mon\b", {"gameboy", "gbc", "gba", "nds"}),
    (r"\bearthbound\b", {"snes"}),
    (r"\bchrono trigger\b", {"snes", "nds"}),
    (r"\bfinal fantasy (iii|vi|3|6)\b", {"snes"}),
    (r"\bconker", {"n64"}),
    (r"\bzelda\b", {"gameboy", "gbc", "gba"}),
]

VALUE_THRESHOLD_CENTS = 5000  # carts worth $50+ are worth faking

CHECKS = {
    "gameboy_family": [
        "Label: should be crisp with a slight sheen, not blurry or matte-printed; edges sit flush in the recess.",
        "Shell: correct plastic color and texture; molded text and the Nintendo logo on the back are sharp.",
        "Screw: originals use a security screw (game-bit or tri-wing); a Phillips screw is a red flag.",
        "Board: if you can see it, look for a Nintendo-marked PCB with a date code, not a single glob-top chip.",
        "Boot it: a missing or wrong Nintendo logo at startup, or saves that don't stick, suggest a fake.",
    ],
    "snes_n64": [
        "Label: compare print sharpness and color to a known-real copy; glossy or pixelated labels are suspect.",
        "Back sticker and molded text: check the code sticker and the embossed Nintendo text are present and crisp.",
        "Screws: originals use security (game-bit) screws, not Phillips.",
        "Board: a Nintendo-marked board with date codes; mismatched or unlabeled chips mean a repro.",
        "Weight and fit: repros often feel lighter or seat loosely in the console.",
    ],
    "other_cart": [
        "Compare the label, shell color, and molded text against photos of a verified copy.",
        "Check for security screws and an original manufacturer board if it can be opened.",
        "Test it in a console before paying.",
    ],
}


def _family(console_key):
    if console_key in {"gameboy", "gbc", "gba"}:
        return "gameboy_family"
    if console_key in {"snes", "n64", "nes", "genesis"}:
        return "snes_n64"
    return "other_cart"


def assess(title, console_key, media, value_cents=None):
    """Return {"level": "high"|"check"|None, "reason": str, "checks": [...]}."""
    if media != "cart":
        return {"level": None, "reason": "", "checks": []}
    t = (title or "").lower()
    for pattern, consoles in HIGH_RISK_PATTERNS:
        if re.search(pattern, t) and (not console_key or console_key in consoles):
            return {"level": "high",
                    "reason": "Commonly reproduced title — verify before buying.",
                    "checks": CHECKS[_family(console_key)]}
    if value_cents and value_cents >= VALUE_THRESHOLD_CENTS:
        return {"level": "check",
                "reason": "Valuable cart — worth a quick authenticity check.",
                "checks": CHECKS[_family(console_key)]}
    return {"level": None, "reason": "", "checks": []}
