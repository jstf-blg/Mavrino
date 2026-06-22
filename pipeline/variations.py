"""
pipeline/variations.py
───────────────────────
Seeded variation engine — kills the "template feel" at scale.

Every post hashes its own slug/keyword to deterministically pick a layout recipe,
an editorial persona (voice + byline), and inherits its vertical's accent colour.
Deterministic so a post is stable on regeneration, but diverse across the site.
"""

import hashlib


def _seed(text: str, salt: str = "") -> int:
    return int(hashlib.md5(f"{salt}|{text}".encode()).hexdigest(), 16)


def pick(text: str, options: list, salt: str = ""):
    """Deterministically pick one option for a given key + salt."""
    return options[_seed(text, salt) % len(options)] if options else None


# ── Editorial voice (one honest brand byline; rotated STYLES avoid a templated feel) ─
# We do NOT use fake named individual reviewers (fabricated bios + stock-photo faces) —
# readers distrust them and Google penalises them. Every post is bylined "Mavrino
# Editorial", the honest voice of the publication. We still rotate the writing STYLE
# below so posts don't all read identically — same byline, varied delivery.
_EDITORIAL_BIO = (
    "Mavrino ranks products by analysing thousands of real customer reviews — with "
    "bias-corrected ratings and a transparent confidence score, not recycled manufacturer "
    "specs. Our guides are written with AI assistance, grounded only in real data."
)

PERSONAS = [
    {"name": "Mavrino Editorial", "bio": _EDITORIAL_BIO,
     "voice": "Write in Mavrino's house voice: blunt, practical and value-first. Skeptical of marketing "
              "claims, quick to flag overpriced features, focused on what a normal buyer actually needs."},
    {"name": "Mavrino Editorial", "bio": _EDITORIAL_BIO,
     "voice": "Write in Mavrino's house voice: thorough and detail-driven, comfortable with specs and "
              "performance numbers but always translating them into plain, real-world terms."},
    {"name": "Mavrino Editorial", "bio": _EDITORIAL_BIO,
     "voice": "Write in Mavrino's house voice: warm, budget-savvy and household-minded — emphasise value, "
              "running costs, and which corners are safe to cut versus which aren't."},
    {"name": "Mavrino Editorial", "bio": _EDITORIAL_BIO,
     "voice": "Write in Mavrino's house voice: measured and reliability-focused — weigh build quality, "
              "longevity and repairability over flashy features, and flag products that won't last."},
    {"name": "Mavrino Editorial", "bio": _EDITORIAL_BIO,
     "voice": "Write in Mavrino's house voice: friendly and experience-led — focus on ease of use, the "
              "everyday details of living with the product, noise, and cleanup."},
]


def persona_for(key: str) -> dict:
    return pick(key, PERSONAS, salt="persona")


# ── Per-vertical accent colours (each category looks distinct) ──────────────────
ACCENTS = {
    "Kitchen":            "#b8431a",   # terracotta / rust
    "Home":               "#3f7d5c",   # sage / emerald
    "Travel":             "#2f6f9f",   # ocean blue
    "Home Office":        "#5a4fcf",   # indigo
    "Fitness & Wellness": "#2a9d8f",   # teal
    "Outdoors":           "#7a8b2e",   # olive / moss
    "EV & Mobility":      "#2563eb",   # electric blue
}
DEFAULT_ACCENT = "#b8431a"


def accent_for(vertical: str) -> str:
    return ACCENTS.get(vertical or "", DEFAULT_ACCENT)


def vertical_for_keyword(keyword: str) -> str | None:
    """Map a keyword to its main vertical via the taxonomy hierarchy."""
    try:
        import taxonomy_manager as tm
        hierarchy = tm.CATEGORY_HIERARCHY
    except Exception:
        return None
    kw = (keyword or "").lower()
    best, best_len = None, 0
    for vertical, children in hierarchy.items():
        for child in children:
            c = child.lower()
            if c in kw and len(c) > best_len:
                best, best_len = vertical, len(c)
    return best


# ── Layout recipes (vary section order + which optional sections appear) ────────
# disclosure + hero are always prepended; author box always appended. Recipes
# reorder/select the MIDDLE sections so no two posts share the same skeleton.
LAYOUTS = [
    {"name": "classic",     "sections": ["intro", "top_pick", "cards", "buying_guide", "faq"]},
    {"name": "table_first", "sections": ["intro", "comparison_table", "top_pick", "cards", "faq"]},
    {"name": "guide_first", "sections": ["intro", "buying_guide", "top_pick", "cards", "faq"]},
    {"name": "quick_picks", "sections": ["intro", "top_pick", "cards", "comparison_table", "buying_guide"]},
]


def layout_for(key: str) -> dict:
    return pick(key, LAYOUTS, salt="layout")
