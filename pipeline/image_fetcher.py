"""
pipeline/image_fetcher.py
──────────────────────────
Fetches relevant product images from Unsplash API.
Used as placeholder images until Amazon PA-API images are available.

Free tier: 50 requests/hour — plenty for our pipeline.
"""

import os, requests, json, time, hashlib
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

UNSPLASH_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")
CACHE_DIR    = Path("config/image_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Keyword → better search terms for Unsplash
SEARCH_MAP = {
    "air fryer":      "air fryer kitchen appliance",
    "blender":        "blender kitchen smoothie",
    "coffee maker":   "coffee maker kitchen brewing",
    "coffee machine": "coffee machine espresso",
    "food processor": "food processor kitchen",
    "stand mixer":    "stand mixer baking kitchen",
    "toaster oven":   "toaster oven kitchen countertop",
    "electric kettle":"electric kettle kitchen",
    "robot vacuum":   "robot vacuum cleaner floor",
    "air purifier":   "air purifier home living room",
    "mattress":       "mattress bedroom comfortable",
    "sofa":           "sofa living room couch",
    "standing desk":  "standing desk home office",
    "office chair":   "office chair ergonomic workspace",
    "luggage":        "luggage travel suitcase",
    "backpack":       "backpack travel bag",
    "massage gun":    "massage gun fitness recovery",
    "treadmill":      "treadmill fitness exercise",
    "kitchen":        "modern kitchen appliances",
    "home":           "modern home interior",
    "travel":         "travel adventure lifestyle",
    "fitness":        "fitness workout healthy",
}


def get_search_term(keyword: str) -> str:
    """Map a product keyword to the best Unsplash search term."""
    kw = keyword.lower()
    for key, term in SEARCH_MAP.items():
        if key in kw:
            return term
    # Fall back to first two words of keyword
    words = kw.split()[:3]
    return " ".join(words) + " product"


def _photo_to_image(photo: dict, search_term: str) -> dict:
    """Map a raw Unsplash photo object to our compact image dict."""
    return {
        "url":               photo["urls"]["regular"],
        "url_small":         photo["urls"]["small"],
        "url_thumb":         photo["urls"]["thumb"],
        "photographer":      photo["user"]["name"],
        "photographer_url":  photo["user"]["links"]["html"],
        "unsplash_url":      photo["links"]["html"],
        "alt":               photo.get("alt_description") or search_term,
        "width":             photo["width"],
        "height":            photo["height"],
    }


def _fetch_pool(search_term: str, orientation: str = "landscape",
                n: int = 30, page: int = 1) -> list[dict]:
    """Fetch and cache a *pool* of Unsplash images for a search term + page.

    Caching the whole result set (not just the first hit) lets callers pick a
    distinct image per item. Pagination lets us keep finding fresh, never-reused
    hero images even for categories with many posts.
    """
    if not UNSPLASH_KEY or UNSPLASH_KEY == "your_key_here":
        return []

    cache_key  = f"{search_term.replace(' ', '_')[:32]}_{orientation}_p{page}"
    cache_file = CACHE_DIR / f"{cache_key}.json"

    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            if time.time() - cached.get("cached_at", 0) < 7 * 86400 and cached.get("images"):
                return cached["images"]
        except Exception:
            pass

    try:
        r = requests.get(
            "https://api.unsplash.com/search/photos",
            params={"query": search_term, "per_page": min(n, 30), "page": page,
                    "orientation": orientation, "content_filter": "high"},
            headers={"Authorization": f"Client-ID {UNSPLASH_KEY}"},
            timeout=10,
        )
        r.raise_for_status()
        images = [_photo_to_image(p, search_term) for p in r.json().get("results", [])]
        if images:
            cache_file.write_text(json.dumps({"images": images, "cached_at": time.time()}, indent=2))
        return images
    except Exception as e:
        print(f"  [images] Unsplash error for '{search_term}': {e}")
        return []


# ── Global registry of already-used hero images (never reuse one) ──────────────
USED_HEROES_FILE = CACHE_DIR.parent / "used_heroes.json"


def _load_used_heroes() -> set:
    try:
        return set(json.loads(USED_HEROES_FILE.read_text()))
    except Exception:
        return set()


def _save_used_heroes(used: set):
    try:
        USED_HEROES_FILE.write_text(json.dumps(sorted(used), indent=2))
    except Exception:
        pass


def mark_hero_used(image: dict):
    """Record a hero image so it is never selected again."""
    if image and image.get("url"):
        used = _load_used_heroes()
        used.add(image["url"])
        _save_used_heroes(used)


def fetch_image(keyword: str, orientation: str = "landscape") -> dict | None:
    """Return the lead (best) Unsplash image for a keyword, or None."""
    pool = _fetch_pool(get_search_term(keyword), orientation)
    return pool[0] if pool else None


def get_hero_image(keyword: str, unique: bool = True, max_pages: int = 4) -> dict | None:
    """Get a wide hero image — guaranteed never to repeat a previously used one.

    Walks Unsplash result pages for the keyword's category until it finds an image
    whose URL isn't already in the used-heroes registry, reserves it, and returns
    it. Falls back to the lead image only if every candidate is exhausted.
    """
    term = get_search_term(keyword)
    if not unique:
        pool = _fetch_pool(term, "landscape")
        return pool[0] if pool else None

    used = _load_used_heroes()
    for page in range(1, max_pages + 1):
        pool = _fetch_pool(term, "landscape", page=page)
        if not pool:
            break
        for img in pool:
            if img.get("url") and img["url"] not in used:
                used.add(img["url"])
                _save_used_heroes(used)
                return img
    # Everything exhausted (very unlikely) — return the lead image without reserving.
    pool = _fetch_pool(term, "landscape")
    return pool[0] if pool else None


def get_product_image(product_title: str, fallback_keyword: str = "", variant_key: str = "") -> str:
    """
    Get a distinct, relevant image URL for a product.

    Until the Amazon PA-API supplies real per-product photos, we pull from the
    Unsplash category pool but pick a *different* image per product (keyed on the
    product title/ASIN) so a roundup never shows the same photo on every card.
    Index 0 is reserved for the post hero to avoid an immediate duplicate.
    Returns an image URL string, or empty string when nothing is available.
    """
    # Build the pool from the post's category keyword (consistent, relevant
    # results) and only vary which image is selected per product.
    pool_basis = fallback_keyword or product_title
    pool       = _fetch_pool(get_search_term(pool_basis), orientation="landscape")
    if not pool and product_title and product_title != pool_basis:
        pool = _fetch_pool(get_search_term(product_title), orientation="landscape")
    if not pool:
        return ""
    key = variant_key or product_title or fallback_keyword or "0"
    if len(pool) > 1:
        idx = 1 + (int(hashlib.md5(key.encode()).hexdigest(), 16) % (len(pool) - 1))
    else:
        idx = 0
    return pool[idx]["url_small"]


def build_image_credit(image: dict) -> str:
    """Build a proper Unsplash attribution string (required by their license)."""
    if not image:
        return ""
    return (
        f'Photo by <a href="{image["photographer_url"]}?utm_source=mavrino&utm_medium=referral" '
        f'rel="nofollow">{image["photographer"]}</a> on '
        f'<a href="{image["unsplash_url"]}?utm_source=mavrino&utm_medium=referral" '
        f'rel="nofollow">Unsplash</a>'
    )


if __name__ == "__main__":
    # Test
    print("Testing Unsplash image fetch...")
    img = fetch_image("air fryer")
    if img:
        print(f"Got image: {img['url_small']}")
        print(f"By: {img['photographer']}")
    else:
        print("No image returned — check UNSPLASH_ACCESS_KEY in .env")
