"""
pipeline/01_keyword_discovery.py
─────────────────────────────────
Pulls trending product keywords from:
  1. Google Trends (via SerpApi) — rising searches in our niches
  2. Amazon Bestsellers (via Apify) — what's actually selling right now

Scores each keyword by:
  - Trend momentum (rising = higher score)
  - Search intent (buyer keywords score higher)
  - Competition proxy (long-tail = lower competition)

Outputs: config/keyword_queue.json — sorted list of posts to write
"""

import os, json, time, hashlib, requests
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

SERPAPI_KEY   = os.getenv("SERPAPI_KEY")
APIFY_TOKEN   = os.getenv("APIFY_TOKEN")
NICHES        = [n.strip() for n in os.getenv("NICHES", "air fryers").split(",")]
QUEUE_FILE    = Path("config/keyword_queue.json")
DONE_FILE     = Path("config/keywords_done.json")
QUEUE_FILE.parent.mkdir(exist_ok=True)

# ── Buyer-intent modifiers that signal commercial/transactional queries ────────
BUYER_MODIFIERS = [
    "best", "vs", "review", "top", "cheap", "under $50", "under $100",
    "under $150", "under $200", "for small kitchen", "for large family",
    "for beginners", "for apartment", "quiet", "compact", "large capacity",
    "cordless", "stainless steel", "2025", "2026",
]

COMPARISON_TEMPLATES = [
    "{a} vs {b}",
    "best {niche} under $100",
    "best {niche} under $150",
    "best {niche} under $200",
    "best {niche} for small kitchen",
    "best {niche} for large family",
    "best {niche} for beginners",
    "best compact {niche}",
    "best quiet {niche}",
    "best {niche} 2026",
    "{niche} buying guide",
    "cheapest {niche} that actually works",
    "best {niche} reviewed",
]


def fetch_google_trends(keyword: str) -> list[dict]:
    """Pull rising related queries for a keyword from Google Trends via SerpApi."""
    if not SERPAPI_KEY or SERPAPI_KEY == "your_serpapi_key_here":
        print(f"  [trends] No SerpApi key — using fallback for '{keyword}'")
        return _generate_fallback_keywords(keyword)

    url = "https://serpapi.com/search"
    params = {
        "engine":    "google_trends",
        "q":         keyword,
        "data_type": "RELATED_QUERIES",
        "geo":       "US",
        "api_key":   SERPAPI_KEY,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        rising = data.get("related_queries", {}).get("rising", [])
        results = []
        for item in rising[:20]:
            query = item.get("query", "")
            value = item.get("value", 0)
            if query and _is_buyer_intent(query):
                results.append({
                    "keyword":  query,
                    "source":   "google_trends_rising",
                    "score":    min(100, int(str(value).replace(",", "").replace("+", "")) // 10)
                                if str(value).replace(",", "").replace("+", "").isdigit()
                                else 80,  # "Breakout" queries score high
                })
        print(f"  [trends] '{keyword}' → {len(results)} rising buyer queries")
        return results
    except Exception as e:
        print(f"  [trends] Error for '{keyword}': {e}")
        return _generate_fallback_keywords(keyword)


def fetch_amazon_bestsellers(category_keyword: str) -> list[dict]:
    """Scrape Amazon bestseller lists via Apify to find trending products."""
    if not APIFY_TOKEN or APIFY_TOKEN == "your_apify_token_here":
        print(f"  [amazon] No Apify token — using fallback for '{category_keyword}'")
        return _generate_amazon_fallback(category_keyword)

    # Apify Amazon Bestsellers Actor
    url = f"https://api.apify.com/v2/acts/automation-lab~amazon-bestsellers-scraper/run-sync-get-dataset-items"
    params = {"token": APIFY_TOKEN}
    payload = {
        "searchKeyword": category_keyword,
        "maxItems":      20,
        "country":       "US",
    }
    try:
        r = requests.post(url, json=payload, params=params, timeout=60)
        r.raise_for_status()
        items = r.json()
        results = []
        seen_titles = set()
        for item in items:
            title = item.get("name", item.get("title", ""))
            asin  = item.get("asin", "")
            rank  = item.get("bestsellersRank", item.get("rank", 999))
            rating = float(item.get("rating", 0))
            reviews = int(item.get("reviewsCount", item.get("reviewCount", 0)))

            if not title or not asin or title in seen_titles:
                continue
            seen_titles.add(title)

            # Generate keyword variants from the product title
            clean = _clean_product_title(title)
            keywords = [
                f"{clean} review",
                f"best {category_keyword} {_extract_brand(title)}",
                f"{clean} vs",  # will get paired later
            ]
            for kw in keywords:
                if len(kw) > 10:
                    results.append({
                        "keyword":  kw,
                        "source":   "amazon_bestseller",
                        "asin":     asin,
                        "score":    _score_product(rank, rating, reviews),
                        "rank":     rank,
                        "rating":   rating,
                        "reviews":  reviews,
                        "title":    title,
                    })

        print(f"  [amazon] '{category_keyword}' → {len(results)} product keywords")
        return results
    except Exception as e:
        print(f"  [amazon] Error for '{category_keyword}': {e}")
        return _generate_amazon_fallback(category_keyword)


def generate_comparison_pairs(products: list[dict]) -> list[dict]:
    """Generate 'X vs Y' keywords from pairs of bestselling products."""
    pairs = []
    titles = [p for p in products if p.get("title") and p.get("asin")]

    for i in range(min(len(titles), 8)):
        for j in range(i + 1, min(len(titles), 8)):
            a = _clean_product_title(titles[i]["title"])
            b = _clean_product_title(titles[j]["title"])
            if a and b and a != b:
                pairs.append({
                    "keyword":   f"{a} vs {b}",
                    "source":    "comparison_pair",
                    "asin_a":    titles[i].get("asin"),
                    "asin_b":    titles[j].get("asin"),
                    "score":     75,
                    "post_type": "comparison",
                })
    return pairs


def generate_template_keywords(niche: str) -> list[dict]:
    """Generate long-tail keywords from templates — these are low competition gold."""
    results = []
    for template in COMPARISON_TEMPLATES:
        kw = template.replace("{niche}", niche)
        if "{a}" not in kw and "{b}" not in kw:
            results.append({
                "keyword":  kw,
                "source":   "template",
                "score":    60,
                "post_type": _infer_post_type(kw),
            })
    return results


def _score_product(rank, rating, reviews) -> int:
    """Score a product 0–100 based on bestseller rank, rating and review count."""
    rank_score    = max(0, 100 - (rank or 999))
    rating_score  = int((float(rating or 0) / 5.0) * 30)
    review_score  = min(30, int((reviews or 0) / 100))
    return min(100, rank_score + rating_score + review_score)


def _is_buyer_intent(keyword: str) -> bool:
    kw = keyword.lower()
    return any(m in kw for m in ["best", "vs", "review", "buy", "cheap", "top", "under"])


def _clean_product_title(title: str) -> str:
    """Shorten a product title to brand + model only."""
    import re
    # Remove anything in parentheses and truncate after comma
    t = re.sub(r"\(.*?\)", "", title).split(",")[0].strip()
    # Remove common suffixes
    for suffix in [" - ", " | ", "  "]:
        if suffix in t:
            t = t.split(suffix)[0].strip()
    return t[:60].strip()


def _extract_brand(title: str) -> str:
    return title.split()[0] if title else ""


def _infer_post_type(keyword: str) -> str:
    kw = keyword.lower()
    if " vs " in kw:           return "comparison"
    if kw.startswith("best "):  return "roundup"
    if "review" in kw:         return "review"
    if "guide" in kw:          return "guide"
    if "under $" in kw:        return "budget_roundup"
    return "roundup"


def _generate_fallback_keywords(niche: str) -> list[dict]:
    """Fallback if no API keys set — generates deterministic keyword list."""
    results = []
    for mod in BUYER_MODIFIERS[:8]:
        results.append({
            "keyword": f"{mod} {niche}",
            "source":  "fallback",
            "score":   50,
        })
    return results


def _generate_amazon_fallback(niche: str) -> list[dict]:
    return [
        {"keyword": f"best {niche} review",   "source": "fallback", "score": 55, "post_type": "review"},
        {"keyword": f"top {niche} 2026",       "source": "fallback", "score": 55, "post_type": "roundup"},
        {"keyword": f"cheap {niche} under $100", "source": "fallback", "score": 50, "post_type": "budget_roundup"},
    ]


def load_done() -> set:
    if DONE_FILE.exists():
        return set(json.loads(DONE_FILE.read_text()))
    return set()


def save_done(done: set):
    DONE_FILE.write_text(json.dumps(list(done), indent=2))


def dedupe_and_score(keywords: list[dict], done: set) -> list[dict]:
    """Remove duplicates, already-done keywords, and sort by score desc."""
    seen = set()
    unique = []
    for kw in keywords:
        key = kw["keyword"].lower().strip()
        slug = hashlib.md5(key.encode()).hexdigest()[:8]
        if key not in seen and slug not in done:
            seen.add(key)
            kw["slug"]      = slug
            kw["post_type"] = kw.get("post_type") or _infer_post_type(key)
            unique.append(kw)
    unique.sort(key=lambda x: x.get("score", 0), reverse=True)
    return unique


def run():
    print(f"\n{'='*50}")
    print(f" Keyword Discovery — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}")

    done     = load_done()
    all_kws  = []
    products = []  # collect for comparison pair generation

    for niche in NICHES:
        print(f"\n[niche] {niche}")

        # 1. Google Trends rising queries
        trends = fetch_google_trends(niche)
        all_kws.extend(trends)
        time.sleep(1)  # rate limit courtesy

        # 2. Amazon bestsellers
        bestsellers = fetch_amazon_bestsellers(niche)
        all_kws.extend(bestsellers)
        products.extend([b for b in bestsellers if b.get("asin")])
        time.sleep(1)

        # 3. Template-generated long-tail
        templates = generate_template_keywords(niche)
        all_kws.extend(templates)

    # 4. VS comparison pairs from top products
    pairs = generate_comparison_pairs(products)
    all_kws.extend(pairs)
    print(f"\n[pairs] Generated {len(pairs)} comparison pairs")

    # Dedupe, filter done, score
    queue = dedupe_and_score(all_kws, done)

    # Load existing queue and merge (new items go to front if high score)
    existing = []
    if QUEUE_FILE.exists():
        existing = json.loads(QUEUE_FILE.read_text())

    existing_slugs = {k["slug"] for k in existing}
    new_only = [k for k in queue if k["slug"] not in existing_slugs]
    merged   = new_only + existing  # new high-score items first
    merged.sort(key=lambda x: x.get("score", 0), reverse=True)

    QUEUE_FILE.write_text(json.dumps(merged, indent=2))

    print(f"\n{'='*50}")
    print(f" Queue: {len(merged)} keywords total ({len(new_only)} new)")
    print(f" Top 5:")
    for k in merged[:5]:
        print(f"   [{k.get('score',0):3d}] {k['keyword']}  ({k.get('post_type','?')})")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    run()
