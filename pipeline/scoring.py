"""
pipeline/scoring.py
────────────────────
The Mavrino Score — a proprietary 0–10 rating computed from real signals, not
marketing copy. It's the data moat: a number only we publish, which makes every
post original by construction and is strong featured-snippet bait.

Inputs we already have:
  • Quality      — average customer rating (0–5)
  • Confidence   — how many verified reviews back that rating (log-scaled)
  • Value        — rating-per-dollar versus the product's category peers

When the PA-API lands, the same function gets richer inputs (price stability,
review velocity, complaint ratio) without changing how it's displayed.
"""

import math


def mavrino_score(product: dict, peers: list = None) -> float:
    """Return a 2.0–10.0 Mavrino Score for a product, judged against its peers."""
    rating  = float(product.get("rating") or 0)
    reviews = int(product.get("review_count") or 0)
    price   = float(product.get("price") or 0)

    quality    = max(0.0, min(1.0, rating / 5.0))
    confidence = min(1.0, math.log10(reviews + 1) / 4.7) if reviews > 0 else 0.0  # ~50k reviews ≈ 1.0

    value = 0.5
    if peers and price > 0:
        rpd = [(float(p.get("rating") or 0) / float(p.get("price") or 1))
               for p in peers if (p.get("price") or 0) > 0]
        best = max(rpd) if rpd else 0
        if best > 0:
            value = max(0.0, min(1.0, (rating / price) / best))

    raw = 0.50 * quality + 0.20 * confidence + 0.30 * value
    return round(2.0 + 8.0 * raw, 1)


def attach_scores(products: list, peers: list = None) -> list:
    """Attach a 'mavrino_score' to each product (judged against the peer pool)."""
    peers = peers or products
    for p in products:
        p["mavrino_score"] = mavrino_score(p, peers)
    return products


def score_label(score: float) -> str:
    """Short qualitative label for a score (used in copy/badges)."""
    if score >= 9.0:   return "Outstanding"
    if score >= 8.0:   return "Excellent"
    if score >= 7.0:   return "Very good"
    if score >= 6.0:   return "Good"
    return "Fair"
