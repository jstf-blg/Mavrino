"""
pipeline/03_content_generator.py
──────────────────────────────────
Calls Claude API (Haiku) to generate post content.

Key design decisions:
  - Passes REAL product data (price, rating, reviews) to Claude
  - Claude synthesises the data — it does NOT make things up
  - Different prompt templates per post type (roundup, comparison, review, guide)
  - Instructs Claude to use actual review quotes from the data
  - Strips AI clichés ("dive into", "comprehensive", "seamlessly")
  - Requests structured JSON output for clean template injection
"""

import os, json, time, random, re
import anthropic
from pathlib import Path
from dotenv import load_dotenv

# "top 5", "top 7 air fryers", etc. → render as a ranked, numbered listicle
LISTICLE_RE = re.compile(r"\btop\s+(\d+)\b", re.IGNORECASE)

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Banned phrases that signal raw AI output — Claude is instructed to avoid these
BANNED_PHRASES = [
    "dive into", "dives into", "diving into",
    "comprehensive", "in-depth", "seamlessly",
    "it's worth noting", "at the end of the day",
    "game-changer", "game changer", "revolutionary",
    "when it comes to", "let's explore",
    "in conclusion", "to summarise", "to summarize",
    "overall, this", "all in all",
    "without further ado", "that being said",
    "needless to say", "first and foremost",
]

# ── Post type prompt templates ─────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a consumer product expert and reviewer for a US affiliate site.
You write honest, direct product content based on real data — prices, ratings, and actual customer reviews.
You NEVER invent specifications or experiences.
You write for real people making purchase decisions, not for search engines.
Your writing is direct, specific, and opinionated. You give clear verdicts.

CHARISMA — every post must radiate BOTH competence and warmth (this is what makes a reader trust the
pick enough to click through and buy):
- COMPETENCE (you clearly know this category): lead with a decisive verdict, not a hedge. Use concrete
  numbers — price, rating, review counts, the Mavrino Score, real specs. BAN hedging language: no
  "might", "could be", "in our opinion", "arguably", "one of the". Make the call.
- WARMTH (you're on the reader's side, not the seller's): name the honest trade-off and who each
  product is NOT for. Frame everything around the reader's goal ("if you want X, get Y"). Trust is
  earned through candour, never hype — no fake urgency, no exaggerated or unverifiable claims.
- These multiply: a confident pick is only believable once you've also admitted a genuine downside. A
  decisive verdict + one honest caveat is the most persuasive pattern you have. Use it everywhere.

SEO (important): The exact target keyword phrase MUST appear naturally in the title AND within the
first 100 words of the intro — ideally in the opening sentence. Open the intro with a clear value
proposition: state plainly what the reader gets from this guide and who it's for. Write naturally for
people first; never keyword-stuff.

DEPTH & AUTHORITY (2026 rankings reward depth + first-hand experience): go deep, never thin. Each product
write-up must read like a genuine hands-on mini-review — real-world performance, the standout strength,
who it suits, the honest limitation — citing concrete numbers (rating, review count, price, Mavrino Score).
Be comprehensive enough that the reader needs no other page, and show experience by referencing what real
owners report and how the picks compare with each other.

SEO MECHANICS:
- Lead with a direct, quotable answer to the query in the first 1-2 sentences (wins featured snippets and
  AI Overview citations).
- Cover the topic semantically — related subtopics, specs, use-cases, and natural keyword variations (no stuffing).
- Skimmable AND deep: clear takeaways up top, real substance below; cut filler (it erodes E-E-A-T).

FORBIDDEN phrases (never use these): {banned}

Always respond with valid JSON only. No markdown fences, no preamble.""".format(
    banned=", ".join(f'"{p}"' for p in BANNED_PHRASES[:12])
)


def _format_product_for_prompt(product: dict) -> str:
    """Summarise a product dict into a compact string for the prompt."""
    ra = product.get("review_analysis", {})
    lines = [
        f"ASIN: {product.get('asin', 'N/A')}",
        f"Title: {product.get('title', 'N/A')}",
        f"Brand: {product.get('brand', 'N/A')}",
        f"Price: ${product.get('price', 0):.2f}",
        f"Rating: {product.get('rating', 0)}/5 ({product.get('review_count', 0):,} reviews)",
        f"Mavrino Score: {product.get('mavrino_score', 'N/A')}/10 (our proprietary score — you may reference it)",
        f"Features: {'; '.join(product.get('features', [])[:4])}",
        f"% Positive reviews: {ra.get('pct_positive', 0)}%",
        f"Common praise: {', '.join(ra.get('common_praise', [])[:3])}",
        f"Common complaints: {', '.join(ra.get('common_complaints', [])[:3])}",
    ]

    # Add up to 2 real review quotes
    quotes = ra.get("top_quotes", [])
    for i, q in enumerate(quotes[:2]):
        lines.append(f'Real review {i+1} ({q["stars"]}★): "{q["text"][:180]}"')

    return "\n".join(lines)


# ── Prompt builders per post type ─────────────────────────────────────────────

def _title_guidance(keyword: str, n: int) -> str:
    """Title instructions: numbered listicle for 'top N', else a rotated style.

    Rotation is keyword-seeded so the same keyword is stable but different
    keywords pick different styles — avoids every post being 'The Best X in Year'.
    """
    if LISTICLE_RE.search(keyword):
        return (f'A numbered listicle headline using the actual count of {n} products, e.g. '
                f'"{n} Best [Category] Worth Buying in [Year]" or "Top {n} [Category], Ranked for [Year]".')

    styles = [
        'Verdict-hook style: "The Best [Category] in [Year]: [punchy 3-5 word verdict]"',
        f'Numbered style: "{n} Best [Category] We\'d Actually Buy in [Year]"',
        'Buyer/use-case style: "Best [Category] for [the specific buyer or use case in the keyword] in [Year]"',
        'Tested-angle style: "We Compared the Top [Category] — Here Are the Best in [Year]"',
    ]
    pick = styles[sum(ord(c) for c in keyword) % len(styles)]
    return f'Use THIS title style (do not default to "The Best X in Year"): {pick}'


def build_roundup_prompt(keyword: str, products: list[dict]) -> str:
    items = products[:7]
    n     = len(items)
    is_listicle = bool(LISTICLE_RE.search(keyword))
    products_text = "\n\n".join(
        f"PRODUCT {i+1}:\n{_format_product_for_prompt(p)}"
        for i, p in enumerate(items)
    )
    heading_hint = (
        '"#1 Best Overall", "#2 Best Value", "#3 ..." — number every pick in rank order'
        if is_listicle else
        '"Best Overall", "Best Budget", "Best for Families", etc — match the keyword\'s angle'
    )
    return f"""Write a {"ranked listicle" if is_listicle else "best-of roundup"} post for "{keyword}" for a US affiliate site.

KEYWORD: {keyword}
TODAY: {time.strftime('%B %Y')}
PRODUCTS TO COVER: {n}

REAL PRODUCT DATA:
{products_text}

TITLE: {_title_guidance(keyword, n)}

Return JSON with this exact structure:
{{
  "title": "<headline following the TITLE guidance above>",
  "meta_description": "120 chars max, includes keyword naturally",
  "intro": "3 substantial paragraphs. (1) A direct hook that answers the query in the first sentence (include the keyword naturally) and names who this guide is for. (2) How we evaluated — the Mavrino Score, real customer-review data, and the buying factors that mattered most (establish credibility and first-hand authority). (3) A quick preview of the shortlist and what sets the top pick apart.",
  "key_takeaways": ["3-5 punchy one-line takeaways — the TL;DR a skimmer needs: the top pick, the best-value pick, the single most important buying factor, and one surprising finding. Each under 15 words. Snippet/AI-Overview friendly."],
  "winner_asin": "ASIN of best overall pick",
  "winner_pitch": "ONE punchy, confident sentence (max 15 words) on why this is THE pick for most people. No hedging.",
  "winner_verdict": "2-3 sentences explaining WHY this product wins. Reference actual rating/review data.",
  "winner_caveat": "ONE honest sentence: the real trade-off, or the buyer who should pick something else. Builds trust.",
  "products": [
    {{
      "asin": "...",
      "heading": {heading_hint},
      "verdict": "A substantial 4-6 sentence hands-on-style mini-review: real-world performance, the standout strength owners praise, how it compares with the others here, who it is ideal for, and the honest limitation. Cite specific numbers (rating, review count, price, Mavrino Score). Read like genuine experience, not a spec list.",
      "who_its_for": "1 sentence describing the ideal buyer",
      "not_for": "1 short sentence: who should skip this one. Honest, specific.",
      "main_pro": "Top praised feature from reviews",
      "main_con": "Top complaint from reviews",
      "quote": "One real review quote from the data (verbatim, under 100 words)"
    }}
  ],
  "buying_guide": "4-5 substantial paragraphs covering the key buying factors in depth — what actually matters and why, the common mistakes buyers make, and how to match a pick to your needs and budget. Practical, specific, and genuinely useful.",
  "bottom_line": "A confident 3-4 sentence closing: restate the top pick and the single reason it wins, name the best-value alternative and who should choose it instead, and end on a clear, decisive recommendation. Warm and authoritative.",
  "faq": [
    {{"q": "question", "a": "answer, 2-3 sentences"}},
    {{"q": "question", "a": "answer, 2-3 sentences"}},
    {{"q": "question", "a": "answer, 2-3 sentences"}},
    {{"q": "question", "a": "answer, 2-3 sentences"}}
  ]
}}

Cover all {n} products in the "products" array{', in ranked order from #1' if is_listicle else ''}."""


def build_comparison_prompt(keyword: str, product_a: dict, product_b: dict) -> str:
    return f"""Write a "{keyword}" comparison post for a US affiliate site.

KEYWORD: {keyword}
TODAY: {time.strftime('%B %Y')}

PRODUCT A:
{_format_product_for_prompt(product_a)}

PRODUCT B:
{_format_product_for_prompt(product_b)}

Return JSON with this exact structure:
{{
  "title": "[Product A] vs [Product B]: Which Should You Buy in [Year]?",
  "meta_description": "120 chars max",
  "intro": "2 paragraphs. State the core difference upfront. Who should buy which.",
  "winner": "ASIN of recommended product for most people",
  "winner_pitch": "ONE confident sentence (max 15 words): which one to buy and why. No hedging.",
  "winner_reason": "2 sentences. Be specific about why.",
  "winner_caveat": "ONE honest sentence: when the OTHER product is actually the better choice.",
  "head_to_head": [
    {{"category": "Price", "product_a": "...", "product_b": "...", "winner": "asin or tie", "note": "1 sentence"}},
    {{"category": "Cooking performance", "product_a": "...", "product_b": "...", "winner": "asin or tie", "note": "1 sentence"}},
    {{"category": "Ease of use", "product_a": "...", "product_b": "...", "winner": "asin or tie", "note": "1 sentence"}},
    {{"category": "Noise level", "product_a": "...", "product_b": "...", "winner": "asin or tie", "note": "1 sentence"}},
    {{"category": "Cleaning", "product_a": "...", "product_b": "...", "winner": "asin or tie", "note": "1 sentence"}},
    {{"category": "Value for money", "product_a": "...", "product_b": "...", "winner": "asin or tie", "note": "1 sentence"}}
  ],
  "product_a_review": {{
    "summary": "A 4-5 sentence hands-on-style mini-review: real-world performance, the standout strength owners praise, who it suits, and the honest limitation. Cite specific numbers (rating, review count, price, Mavrino Score).",
    "best_for": "Who this is ideal for",
    "real_quote": "One genuine review quote"
  }},
  "product_b_review": {{
    "summary": "A 4-5 sentence hands-on-style mini-review: real-world performance, the standout strength owners praise, who it suits, and the honest limitation. Cite specific numbers (rating, review count, price, Mavrino Score).",
    "best_for": "Who this is ideal for",
    "real_quote": "One genuine review quote"
  }},
  "verdict": "2 paragraphs. Final recommendation with specific reasoning. Don't hedge excessively.",
  "faq": [
    {{"q": "question", "a": "answer"}},
    {{"q": "question", "a": "answer"}}
  ]
}}"""


def build_review_prompt(keyword: str, product: dict) -> str:
    ra = product.get("review_analysis", {})
    pos_snippets = ra.get("positive_snippets", [])
    neg_snippets = ra.get("negative_snippets", [])
    pos_text = " | ".join(pos_snippets[:3]) if pos_snippets else "Not available"
    neg_text = " | ".join(neg_snippets[:3]) if neg_snippets else "Not available"

    return f"""Write a product review post for a US affiliate site.

KEYWORD: {keyword}
TODAY: {time.strftime('%B %Y')}

PRODUCT DATA:
{_format_product_for_prompt(product)}

POSITIVE REVIEW SNIPPETS (real customer words):
{pos_text}

CRITICAL REVIEW SNIPPETS (real customer words):
{neg_text}

Return JSON with this exact structure:
{{
  "title": "[Product Name] Review ([Year]): Is It Worth [Price]?",
  "meta_description": "120 chars max",
  "intro": "2 paragraphs. Lead with who should read this and the key question the review answers.",
  "quick_verdict": "2 sentences. Bottom line upfront.",
  "verdict_caveat": "ONE honest sentence: the main drawback to accept, or the buyer who should skip it.",
  "score": {{
    "overall": 8.2,
    "performance": 8.5,
    "ease_of_use": 8.0,
    "value": 7.8,
    "cleaning": 8.5
  }},
  "what_we_like": ["specific point 1", "specific point 2", "specific point 3"],
  "what_we_dont": ["specific criticism 1", "specific criticism 2"],
  "sections": [
    {{"heading": "Build quality and design", "content": "2 paragraphs. Specific."}},
    {{"heading": "Performance in testing", "content": "2 paragraphs. Reference review themes."}},
    {{"heading": "Ease of use", "content": "1-2 paragraphs."}},
    {{"heading": "Cleaning and maintenance", "content": "1 paragraph. Reference common complaints or praise."}},
    {{"heading": "Value for money", "content": "1 paragraph. Compare to similar price-point products."}}
  ],
  "real_owner_say": [
    {{"stars": 5, "quote": "verified review quote from data"}},
    {{"stars": 2, "quote": "critical review quote from data"}}
  ],
  "who_should_buy": "1 sentence.",
  "who_should_skip": "1 sentence.",
  "verdict": "2 paragraphs. Clear recommendation.",
  "faq": [
    {{"q": "question", "a": "answer"}},
    {{"q": "question", "a": "answer"}}
  ]
}}"""


def build_budget_roundup_prompt(keyword: str, products: list[dict]) -> str:
    # Filter to products under the price in the keyword
    price_limit = 200
    for amount in [50, 75, 100, 150, 200]:
        if f"${amount}" in keyword or f"under {amount}" in keyword.lower():
            price_limit = amount
            break

    filtered = [p for p in products if p.get("price", 999) <= price_limit]
    if not filtered:
        filtered = sorted(products, key=lambda x: x.get("price", 999))[:4]

    products_text = "\n\n".join(
        f"PRODUCT {i+1}:\n{_format_product_for_prompt(p)}"
        for i, p in enumerate(filtered[:4])
    )

    return f"""Write a budget roundup post for "{keyword}" for a US affiliate site.

KEYWORD: {keyword}
PRICE CEILING: ${price_limit}
TODAY: {time.strftime('%B %Y')}

PRODUCTS (all under ${price_limit}):
{products_text}

Return JSON with this structure:
{{
  "title": "Best {keyword} in [Year]: Tested and Ranked",
  "meta_description": "120 chars max",
  "intro": "2 paragraphs. Address the trade-offs of buying at this price. What you get, what you give up.",
  "top_pick_asin": "ASIN of best value pick",
  "products": [
    {{
      "asin": "...",
      "rank": 1,
      "heading": "Best Overall Under ${price_limit}",
      "price_note": "e.g. 'Currently $XX — strong value at this price'",
      "verdict": "2 sentences drawing on review data",
      "real_quote": "One genuine review quote from the data",
      "main_pro": "...",
      "main_con": "..."
    }}
  ],
  "what_to_expect": "1 paragraph. Honest about what budget options can and cannot do.",
  "verdict": "1 paragraph. Final recommendation.",
  "faq": [
    {{"q": "Are cheap [product] any good?", "a": "Honest answer based on review data"}},
    {{"q": "question", "a": "answer"}}
  ]
}}"""


# ── Semantic product-fit selection ─────────────────────────────────────────────
# Niche + price are handled deterministically in cache_builder. But ATTRIBUTE and
# USE-CASE angles (quiet, compact, large-capacity, for-small-kitchens, premium…)
# need product knowledge: a loud blender must not appear in a "quiet blenders" post.

_ANGLE_WORDS = [
    "quiet", "silent", "compact", "mini", "portable", "lightweight", "light weight",
    "large capacity", "large", "heavy duty", "heavy-duty", "durable", "premium",
    "high-end", "high end", "professional", "luxury", "stylish", "space saving",
    "space-saving", "easy to clean", "for beginners", "for small", "for large",
    "for famil", "for apartment", "for travel", "for college", "for the money",
]


def keyword_has_angle(keyword: str) -> bool:
    kw = (keyword or "").lower()
    return any(w in kw for w in _ANGLE_WORDS)


def select_products_for_angle(keyword: str, products: list[dict], count: int) -> list[dict]:
    """Return the products that genuinely fit the post's specific angle, ranked
    best-fit first, excluding contradictions (e.g. a loud blender for a 'quiet'
    post). Uses the model's knowledge of these specific models. May return fewer
    than `count` when only some truly fit. Falls back to input order on error.
    """
    if not products:
        return []
    lines = [f'{p.get("asin")} | {p.get("title","")} | {p.get("brand","")} | '
             f'${p.get("price",0):.0f} | {p.get("rating",0)}*' for p in products]
    prompt = (
        f'A reader is looking for: "{keyword}".\n\n'
        'From these candidate products, return the ASINs that GENUINELY fit that specific '
        'angle, ranked best-fit first. EXCLUDE any product that contradicts the angle — e.g. '
        'a loud blender for a "quiet" post, a bulky model for a "compact" post, a basic/cheap '
        'unit for a "premium" post, a small one for a "large capacity" post. Use your knowledge '
        f'of these specific models.\n\nPick up to {count}. If fewer than {count} truly fit, '
        'return only those that do.\n\nCANDIDATES:\n' + "\n".join(lines) +
        '\n\nReturn JSON only: {"asins": ["ASIN", ...]}'
    )
    try:
        msg = client.messages.create(
            model="claude-haiku-4-5", max_tokens=300,
            system="You select products that fit a specific buying angle. Return valid JSON only.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        asins = json.loads(raw).get("asins", [])
        by_asin = {p.get("asin"): p for p in products}
        picked = [by_asin[a] for a in asins if a in by_asin]
        return picked
    except Exception as e:
        print(f"  [select] angle selection failed: {e}")
        return products[:count]


# ── Template angles ────────────────────────────────────────────────────────────
# Each template reuses a base renderer shape (roundup/comparison/review) but appends a
# dedicated ANGLE directive that overrides the generic framing/title so the post reads
# as a true "cheapest" / "splurge" / "every budget" / "worth it" / "most reviewed" piece.

_ANGLE_CHEAPEST = """TEMPLATE — "CHEAPEST THAT ACTUALLY WORK". This OVERRIDES the title/framing above:
- TITLE must lead with "Cheapest", e.g. "The Cheapest [Category] That Actually Work in 2026".
- Frame everything around budget value: for each pick, stress what it does well DESPITE the low price, exactly what (if anything) you give up, and reassure readers they are not buying junk.
- Product headings signal price/value: "#1 Cheapest Overall", "Best Under $[X]", "Cheapest That Lasts".
- The bottom line names the single best cheap buy and the one to avoid."""

_ANGLE_SPLURGE = """TEMPLATE — "MOST EXPENSIVE / WORTH THE SPLURGE?". This OVERRIDES the title/framing above:
- TITLE leads with the premium angle, e.g. "The Most Expensive [Category] on Amazon — Worth the Splurge?".
- For each premium pick, judge honestly whether the high price is justified: what the money actually buys, who should splurge, who should save.
- Headings signal the premium: "The Flagship", "Most Premium Build", "Best High-End Value".
- The bottom line says who the splurge is and isn't for."""

_ANGLE_EVERY_BUDGET = """TEMPLATE — "FOR EVERY BUDGET" (price-TIERED, NOT a generic ranking). This OVERRIDES the framing above:
- TITLE: "The Best [Category] for Every Budget in 2026".
- Organise picks as ASCENDING PRICE TIERS — one best pick per tier. Product headings MUST be budget tiers: "Best Budget (Under $[X])", "Best Mid-Range", "Best Premium", "Best Splurge".
- Each write-up explains who that tier suits and what stepping up gets you.
- Key Takeaways and the Bottom Line map a recommendation to each budget level."""

_ANGLE_WORTH_IT = """TEMPLATE — "IS THE EXPENSIVE ONE WORTH IT?". This OVERRIDES the framing above:
- Product A is the EXPENSIVE pick; Product B is the much cheaper VALUE pick.
- The whole post answers ONE question: is paying more actually worth it?
- The head-to-head table and verdict must state clearly WHEN the premium is justified and when the cheaper pick is the smarter buy, with the price gap front and centre."""

_ANGLE_CVE = """TEMPLATE — "CHEAPEST vs MOST EXPENSIVE". This OVERRIDES the framing above:
- Product A is the CHEAPEST pick; Product B is the MOST EXPENSIVE pick.
- Break down exactly what the extra money buys, where the price jump is and isn't worth it, and the value sweet spot.
- Lead with the dollar gap; the verdict names who should buy the cheap one and who should buy the expensive one."""

_ANGLE_MOST_REVIEWED = """TEMPLATE — "THE MOST-REVIEWED PICK" (the crowd favourite). This OVERRIDES the framing above:
- This product has the most reviews in its category. OPEN with that huge review count as the hook (e.g. "With over [N] reviews…").
- Judge honestly whether popularity equals quality: what thousands of owners consistently praise and complain about, and whether the crowd favourite is genuinely the best buy or just the best-known.
- TITLE: "The Most-Reviewed [Category] on Amazon — Worth the Hype? ([Product] Review)"."""

TEMPLATE_DISPATCH = {
    "cheapest":              ("roundup",    _ANGLE_CHEAPEST),
    "splurge":               ("roundup",    _ANGLE_SPLURGE),
    "every_budget":          ("roundup",    _ANGLE_EVERY_BUDGET),
    "worth_it":              ("comparison", _ANGLE_WORTH_IT),
    "cheapest_vs_expensive": ("comparison", _ANGLE_CVE),
    "most_reviewed":         ("review",     _ANGLE_MOST_REVIEWED),
}


# ── Main generation function ───────────────────────────────────────────────────

def generate_content(
    keyword: str,
    post_type: str,
    products: list[dict],
) -> dict | None:
    """
    Generate post content via Claude API.
    Returns structured dict or None on failure.
    """

    # Template post types map to a base renderer shape + a sharpening ANGLE directive.
    base_type, angle = TEMPLATE_DISPATCH.get(post_type, (post_type, ""))

    # Build prompt based on the base post type
    if base_type == "comparison" and len(products) >= 2:
        prompt = build_comparison_prompt(keyword, products[0], products[1])
    elif base_type == "review" and products:
        prompt = build_review_prompt(keyword, products[0])
    elif base_type == "budget_roundup" and products:
        prompt = build_budget_roundup_prompt(keyword, products)
    else:
        prompt = build_roundup_prompt(keyword, products)
    if angle:
        prompt += "\n\n" + angle

    # Rotate the editorial persona (voice + byline) so posts don't all read alike.
    persona = None
    system  = SYSTEM_PROMPT
    try:
        import variations as var
        persona = var.persona_for(keyword)
        system  = SYSTEM_PROMPT + "\n\nEDITORIAL VOICE — " + persona["voice"]
    except Exception:
        pass

    print(f"  [claude] Generating '{keyword}' ({post_type}){' as ' + persona['name'] if persona else ''}...")

    try:
        message = client.messages.create(
            # Sonnet for depth/quality on the longer reviews (was claude-haiku-4-5 — revert
            # here if cost matters). max_tokens raised for the deeper intro/reviews/closing.
            model="claude-sonnet-4-6",
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text.strip()

        # Strip any accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()

        content = json.loads(raw)
        content["keyword"]   = keyword
        content["post_type"] = base_type   # base shape (roundup/comparison/review) so the renderer dispatches right
        content["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        if persona:
            content["persona"] = persona

        print(f"  [claude] Done — {len(raw)} chars")
        return content

    except json.JSONDecodeError as e:
        print(f"  [claude] JSON parse error: {e}\nRaw: {raw[:200]}")
        return None
    except anthropic.RateLimitError:
        print("  [claude] Rate limited — sleeping 60s")
        time.sleep(60)
        return None
    except Exception as e:
        print(f"  [claude] Error: {e}")
        return None


if __name__ == "__main__":
    # Test without API key — shows prompt structure
    sample_product = {
        "asin": "B08975S94R",
        "title": "Ninja Air Fryer Pro XL 6.5 Qt",
        "brand": "Ninja",
        "price": 129.99,
        "rating": 4.7,
        "review_count": 12450,
        "features": ["6.5 qt", "Max Crisp Technology", "4 presets", "dishwasher safe"],
        "review_analysis": {
            "pct_positive": 87,
            "common_praise": ["heats fast", "easy to clean", "crispy results"],
            "common_complaints": ["loud at high temp", "no viewing window"],
            "top_quotes": [
                {"stars": 5, "title": "Best purchase", "text": "Makes the crispiest fries I've ever had at home. Preheats in under 2 minutes."}
            ],
        },
    }
    print(build_roundup_prompt("best air fryers 2026", [sample_product]))
