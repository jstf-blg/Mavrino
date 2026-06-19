"""post_templates.py — the template mix.

Each template turns a niche into (keyword, post_type, selected products) using a
price- or popularity-based selection rule. Reused by the batch scheduler so the
hourly post stream rotates diverse, non-duplicate angles instead of only "best X"
roundups. Add a template here and it joins the rotation.
"""


def _base(n: str) -> str:
    return n[:-1] if n.endswith("s") else n


def _by_price(products):
    return sorted([p for p in products if p.get("price")], key=lambda p: p["price"])


def _select_cheapest(ps):
    return _by_price(ps)[:4]


def _select_splurge(ps):
    return list(reversed(_by_price(ps)))[:4]


def _select_every_budget(ps):
    sp = _by_price(ps)
    if len(sp) < 2:
        return []
    idx = sorted(set([0, len(sp) // 3, 2 * len(sp) // 3, len(sp) - 1]))
    return [sp[i] for i in idx]


def _select_worth_it(ps):
    sp = _by_price(ps)
    if len(sp) < 2:
        return []
    cheaper = sp[:max(1, len(sp) // 2)]
    value = max(cheaper, key=lambda p: float(p.get("mavrino_score", 0) or p.get("rating", 0)))
    return [sp[-1], value]   # most-expensive vs best-value


def _select_extremes(ps):
    sp = _by_price(ps)
    return [sp[0], sp[-1]] if len(sp) >= 2 else []


def _select_most_reviewed(ps):
    return [max(ps, key=lambda p: int(p.get("review_count", 0) or 0))] if ps else []


TEMPLATES = {
    "cheapest": {
        "keyword": lambda n: f"cheapest {n} that actually work in 2026",
        "post_type": "roundup", "select": _select_cheapest,
    },
    "splurge": {
        "keyword": lambda n: f"most expensive {n} on amazon worth the splurge in 2026",
        "post_type": "roundup", "select": _select_splurge,
    },
    "every_budget": {
        "keyword": lambda n: f"best {n} for every budget in 2026",
        "post_type": "roundup", "select": _select_every_budget,
    },
    "worth_it": {
        "keyword": lambda n: f"is an expensive {_base(n)} worth it in 2026",
        "post_type": "comparison", "select": _select_worth_it,
    },
    "cheapest_vs_expensive": {
        "keyword": lambda n: f"cheapest vs most expensive {_base(n)} in 2026",
        "post_type": "comparison", "select": _select_extremes,
    },
    "most_reviewed": {
        # single-product spotlight on the crowd favourite (highest review count)
        "keyword": lambda n: f"the most reviewed {_base(n)} on amazon — worth the hype in 2026",
        "post_type": "review", "select": _select_most_reviewed,
    },
}


def build(template: str, niche: str, products: list):
    """Return (keyword, post_type, selected_products) for a template+niche, or None."""
    tpl = TEMPLATES.get(template)
    if not tpl:
        return None
    sel = tpl["select"](products)
    need = 2 if tpl["post_type"] == "comparison" else 1
    if len(sel) < need:
        return None
    # Return the TEMPLATE NAME (not the base post_type) so generate_content applies the
    # dedicated ANGLE directive; it resolves the base renderer shape internally.
    return tpl["keyword"](niche), template, sel
