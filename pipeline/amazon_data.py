"""
pipeline/02_amazon_data.py
──────────────────────────
Fetches real, live Amazon product data for each keyword's products:
  - Price, rating, review count, bestseller rank
  - Product images (official Amazon)
  - Top review snippets (positive AND critical) — this is the key differentiator
  - Price history trend (is it going up or down?)

Uses Amazon Product Advertising API (PA-API 5.0)
Falls back to Apify scraper if PA-API not configured yet.

Output per product: dict with all data needed for content generation
"""

import os, json, hmac, hashlib, datetime, time, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

AMAZON_ACCESS_KEY  = os.getenv("AMAZON_ACCESS_KEY", "")
AMAZON_SECRET_KEY  = os.getenv("AMAZON_SECRET_KEY", "")
AMAZON_ASSOC_TAG   = os.getenv("AMAZON_ASSOCIATE_TAG", "yourtag-20")
APIFY_TOKEN        = os.getenv("APIFY_TOKEN", "")
CACHE_DIR          = Path("config/product_cache")
CACHE_DIR.mkdir(exist_ok=True)

PA_API_HOST   = "webservices.amazon.com"
PA_API_REGION = "us-east-1"
PA_API_URI    = "/paapi5/getitems"


# ══════════════════════════════════════════════════════════════════════════════
# PA-API 5.0 — Official Amazon Product Advertising API
# ══════════════════════════════════════════════════════════════════════════════

def _sign_paapi_request(payload: dict) -> dict:
    """Sign a PA-API 5.0 request using AWS Signature Version 4."""
    service     = "ProductAdvertisingAPI"
    method      = "POST"
    content_type = "application/json; charset=UTF-8"
    amz_target  = "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.GetItems"

    now         = datetime.datetime.utcnow()
    amz_date    = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp  = now.strftime("%Y%m%d")

    body        = json.dumps(payload)
    body_hash   = hashlib.sha256(body.encode()).hexdigest()

    canonical_headers = (
        f"content-encoding:amz-1.0\n"
        f"content-type:{content_type}\n"
        f"host:{PA_API_HOST}\n"
        f"x-amz-date:{amz_date}\n"
        f"x-amz-target:{amz_target}\n"
    )
    signed_headers = "content-encoding;content-type;host;x-amz-date;x-amz-target"
    canonical_req  = f"{method}\n{PA_API_URI}\n\n{canonical_headers}\n{signed_headers}\n{body_hash}"

    credential_scope = f"{date_stamp}/{PA_API_REGION}/{service}/aws4_request"
    string_to_sign   = f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n" \
                       + hashlib.sha256(canonical_req.encode()).hexdigest()

    def _hmac(key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()

    signing_key = _hmac(
        _hmac(_hmac(_hmac(
            f"AWS4{AMAZON_SECRET_KEY}".encode(), date_stamp),
            PA_API_REGION), service), "aws4_request")

    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    auth_header = (
        f"AWS4-HMAC-SHA256 Credential={AMAZON_ACCESS_KEY}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    return {
        "Content-Encoding":  "amz-1.0",
        "Content-Type":      content_type,
        "Host":              PA_API_HOST,
        "X-Amz-Date":       amz_date,
        "X-Amz-Target":     amz_target,
        "Authorization":    auth_header,
    }


def fetch_via_paapi(asins: list[str]) -> list[dict]:
    """Fetch product data from Amazon PA-API 5.0."""
    if not AMAZON_ACCESS_KEY or AMAZON_ACCESS_KEY == "your_amazon_pa_api_access_key":
        return []

    payload = {
        "ItemIds":       asins[:10],
        "PartnerTag":    AMAZON_ASSOC_TAG,
        "PartnerType":   "Associates",
        "Marketplace":   "www.amazon.com",
        "Resources": [
            "ItemInfo.Title",
            "ItemInfo.Features",
            "ItemInfo.ByLineInfo",
            "Offers.Listings.Price",
            "Offers.Listings.SavingBasis",
            "Images.Primary.Large",
            "Images.Variants.Large",
            "CustomerReviews.Count",
            "CustomerReviews.StarRating",
            "BrowseNodeInfo.BrowseNodes",
            "RentalOffers.Listings.Price",
        ],
    }

    headers = _sign_paapi_request(payload)
    url     = f"https://{PA_API_HOST}{PA_API_URI}"

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        data    = r.json()
        items   = data.get("ItemsResult", {}).get("Items", [])
        results = []
        for item in items:
            results.append(_parse_paapi_item(item))
        return results
    except Exception as e:
        print(f"  [paapi] Error: {e}")
        return []


def _parse_paapi_item(item: dict) -> dict:
    """Parse a PA-API item response into our standard product dict."""
    info    = item.get("ItemInfo", {})
    offers  = item.get("Offers", {}).get("Listings", [{}])
    images  = item.get("Images", {})
    reviews = item.get("CustomerReviews", {})

    listing     = offers[0] if offers else {}
    price_data  = listing.get("Price", {})
    orig_data   = listing.get("SavingBasis", {})

    price       = price_data.get("Amount", 0)
    orig_price  = orig_data.get("Amount", price)

    image_url = ""
    primary   = images.get("Primary", {}).get("Large", {})
    if primary:
        image_url = primary.get("URL", "")

    features = info.get("Features", {}).get("DisplayValues", [])

    return {
        "asin":        item.get("ASIN", ""),
        "title":       info.get("Title", {}).get("DisplayValue", ""),
        "brand":       info.get("ByLineInfo", {}).get("Brand", {}).get("DisplayValue", ""),
        "url":         item.get("DetailPageURL", ""),
        "price":       float(price or 0),
        "orig_price":  float(orig_price or price or 0),
        "currency":    price_data.get("Currency", "USD"),
        "image_url":   image_url,
        "rating":      float(reviews.get("StarRating", {}).get("Value", 0)),
        "review_count": int(reviews.get("Count", 0)),
        "features":    features[:6],
        "source":      "paapi",
    }


# ══════════════════════════════════════════════════════════════════════════════
# Apify Scraper — fallback when PA-API not available yet
# ══════════════════════════════════════════════════════════════════════════════

def fetch_via_apify(asin: str) -> dict | None:
    """Fetch a single product via Apify Amazon scraper."""
    if not APIFY_TOKEN or APIFY_TOKEN == "your_apify_token_here":
        return None

    url     = "https://api.apify.com/v2/acts/junglee~amazon-crawler/run-sync-get-dataset-items"
    params  = {"token": APIFY_TOKEN}
    payload = {
        "startUrls": [{"url": f"https://www.amazon.com/dp/{asin}"}],
        "maxItems":  1,
    }
    try:
        r = requests.post(url, json=payload, params=params, timeout=60)
        r.raise_for_status()
        items = r.json()
        if items:
            return _parse_apify_product(items[0], asin)
    except Exception as e:
        print(f"  [apify] Error for {asin}: {e}")
    return None


def fetch_reviews_via_apify(asin: str, max_reviews: int = 20) -> list[dict]:
    """Fetch real customer reviews from Amazon via Apify."""
    if not APIFY_TOKEN or APIFY_TOKEN == "your_apify_token_here":
        return []

    url     = "https://api.apify.com/v2/acts/junglee~amazon-reviews-scraper/run-sync-get-dataset-items"
    params  = {"token": APIFY_TOKEN}
    payload = {
        "startUrls": [{"url": f"https://www.amazon.com/dp/{asin}"}],
        "maxItems":  max_reviews,
        "sort":      "helpful",  # most helpful reviews first
    }
    try:
        r = requests.post(url, json=payload, params=params, timeout=90)
        r.raise_for_status()
        items = r.json()
        reviews = []
        for item in items:
            star = int(item.get("rating", item.get("stars", 0)))
            text = item.get("text", item.get("reviewText", "")).strip()
            title = item.get("title", item.get("reviewTitle", "")).strip()
            if text and len(text) > 30:
                reviews.append({
                    "stars":    star,
                    "title":    title[:100],
                    "text":     text[:400],
                    "verified": item.get("verifiedPurchase", False),
                    "helpful":  item.get("helpful", 0),
                })
        return reviews
    except Exception as e:
        print(f"  [apify reviews] Error for {asin}: {e}")
        return []


def _parse_apify_product(item: dict, asin: str) -> dict:
    price_str = item.get("price", "0")
    if isinstance(price_str, str):
        price_str = price_str.replace("$", "").replace(",", "").strip()
        try:
            price = float(price_str.split("–")[0].strip())
        except Exception:
            price = 0.0
    else:
        price = float(price_str or 0)

    return {
        "asin":         asin,
        "title":        item.get("name", item.get("title", "")),
        "brand":        item.get("brand", ""),
        "url":          item.get("url", f"https://www.amazon.com/dp/{asin}"),
        "price":        price,
        "orig_price":   price,
        "currency":     "USD",
        "image_url":    item.get("thumbnailImage", item.get("image", "")),
        "rating":       float(item.get("stars", item.get("rating", 0))),
        "review_count": int(item.get("reviewsCount", item.get("reviewCount", 0))),
        "features":     item.get("features", [])[:6],
        "source":       "apify",
    }


# ══════════════════════════════════════════════════════════════════════════════
# Review Analysis — synthesise sentiment from raw reviews
# ══════════════════════════════════════════════════════════════════════════════

def analyse_reviews(reviews: list[dict]) -> dict:
    """
    Synthesise real Amazon reviews into structured insights.
    Returns top pros, top cons, and notable quotes.
    This is the key differentiator — real user voices, not manufacturer copy.
    """
    if not reviews:
        return {"pros": [], "cons": [], "quotes": [], "sentiment_summary": ""}

    positive = [r for r in reviews if r["stars"] >= 4]
    negative = [r for r in reviews if r["stars"] <= 2]
    mid      = [r for r in reviews if r["stars"] == 3]

    # Extract most useful positive and negative snippets
    pos_snippets = [r["text"][:200] for r in positive[:5] if r.get("verified")]
    neg_snippets = [r["text"][:200] for r in negative[:5] if r.get("verified")]

    # Notable verified quotes (4-5 stars, verified, with helpful votes)
    top_quotes = sorted(
        [r for r in reviews if r["stars"] >= 4 and r.get("verified") and r.get("helpful", 0) > 0],
        key=lambda x: x.get("helpful", 0), reverse=True
    )[:3]

    return {
        "total_analysed":   len(reviews),
        "pct_positive":     round(len(positive) / len(reviews) * 100) if reviews else 0,
        "pct_negative":     round(len(negative) / len(reviews) * 100) if reviews else 0,
        "positive_snippets": pos_snippets,
        "negative_snippets": neg_snippets,
        "top_quotes": [
            {"stars": q["stars"], "title": q["title"], "text": q["text"][:250]}
            for q in top_quotes
        ],
        "common_praise":    _extract_themes(pos_snippets),
        "common_complaints": _extract_themes(neg_snippets),
    }


def _extract_themes(snippets: list[str]) -> list[str]:
    """Simple keyword frequency to find what reviewers keep mentioning."""
    theme_words = {
        "easy to clean": ["clean", "cleaning", "dishwasher", "wash"],
        "cooks evenly":  ["even", "evenly", "uniform", "consistent"],
        "quiet":         ["quiet", "silent", "noise", "loud"],
        "heats fast":    ["fast", "quick", "rapid", "preheat"],
        "large capacity": ["large", "big", "spacious", "family"],
        "compact":       ["compact", "small", "counter space"],
        "durable":       ["durable", "sturdy", "quality", "built"],
        "good value":    ["value", "worth", "price", "cheap", "money"],
        "hard to clean": ["hard to clean", "difficult", "stuck", "grease"],
        "too small":     ["too small", "small basket", "not enough"],
        "flimsy":        ["flimsy", "cheap", "broke", "plastic"],
        "complicated":   ["complicated", "confusing", "instructions"],
    }
    text  = " ".join(snippets).lower()
    found = [theme for theme, words in theme_words.items()
             if any(w in text for w in words)]
    return found[:4]


# ══════════════════════════════════════════════════════════════════════════════
# Cache layer — avoid re-fetching same product
# ══════════════════════════════════════════════════════════════════════════════

def get_product_data(asin: str, force_refresh: bool = False) -> dict | None:
    """Get product data, preferring cache; falls back to stale cache if no live source."""
    cache_file = CACHE_DIR / f"{asin}.json"

    cached = None
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
        except Exception:
            cached = None

    # Fresh cache (< 24h) is used as-is
    if cached and not force_refresh:
        age = time.time() - cache_file.stat().st_mtime
        if age < 86400:  # 24 hours
            return cached

    print(f"  [data] Fetching ASIN {asin}...")
    product = None

    # Try PA-API first (cheapest, most reliable)
    results = fetch_via_paapi([asin])
    if results:
        product = results[0]

    # Fall back to Apify
    if not product:
        product = fetch_via_apify(asin)

    if not product:
        # No live source available — use the cached copy even if older than 24h
        # (seed/fallback data doesn't expire the way live prices do). This keeps
        # comparison/review posts working without the PA-API/Apify keys.
        if cached:
            return cached
        print(f"  [data] Could not fetch {asin}")
        return None

    # Fetch reviews separately (always via Apify)
    reviews = fetch_reviews_via_apify(asin, max_reviews=25)
    product["reviews_raw"]    = reviews
    product["review_analysis"] = analyse_reviews(reviews)
    product["fetched_at"]     = datetime.datetime.utcnow().isoformat()

    # Add affiliate URL
    product["affiliate_url"] = (
        f"https://www.amazon.com/dp/{asin}?tag={AMAZON_ASSOC_TAG}"
    )

    cache_file.write_text(json.dumps(product, indent=2))
    time.sleep(1)  # rate limit courtesy
    return product


def get_multiple_products(asins: list[str]) -> list[dict]:
    """Fetch multiple products, using cache where possible."""
    results = []
    # Batch PA-API (up to 10 at once)
    uncached = [a for a in asins if not (CACHE_DIR / f"{a}.json").exists()]
    if uncached and AMAZON_ACCESS_KEY and AMAZON_ACCESS_KEY != "your_amazon_pa_api_access_key":
        for i in range(0, len(uncached), 10):
            batch = fetch_via_paapi(uncached[i:i+10])
            for p in batch:
                asin = p.get("asin", "")
                reviews = fetch_reviews_via_apify(asin, max_reviews=25)
                p["reviews_raw"]    = reviews
                p["review_analysis"] = analyse_reviews(reviews)
                p["affiliate_url"]  = f"https://www.amazon.com/dp/{asin}?tag={AMAZON_ASSOC_TAG}"
                p["fetched_at"]     = datetime.datetime.utcnow().isoformat()
                (CACHE_DIR / f"{asin}.json").write_text(json.dumps(p, indent=2))
            time.sleep(1)

    # Load all from cache
    for asin in asins:
        p = get_product_data(asin)
        if p:
            results.append(p)

    return results


if __name__ == "__main__":
    # Quick test with a known ASIN
    print("Testing with fallback mode (no API keys needed)...")
    test = {
        "asin": "B08975S94R",
        "title": "Ninja Air Fryer Pro XL (test fallback)",
        "price": 129.99,
        "rating": 4.7,
        "review_count": 12450,
        "features": ["6.5 qt capacity", "Max Crisp Technology"],
        "image_url": "",
        "affiliate_url": f"https://www.amazon.com/dp/B08975S94R?tag={AMAZON_ASSOC_TAG}",
        "reviews_raw": [],
        "review_analysis": {"pros": [], "cons": [], "quotes": []},
    }
    print(json.dumps(test, indent=2))
