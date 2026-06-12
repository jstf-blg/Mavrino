"""
pipeline/image_fetcher.py
──────────────────────────
Fetches relevant product images from Unsplash API.
Used as placeholder images until Amazon PA-API images are available.

Free tier: 50 requests/hour — plenty for our pipeline.
"""

import os, requests, json, time
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


def fetch_image(keyword: str, orientation: str = "landscape") -> dict | None:
    """
    Fetch a relevant image URL from Unsplash for a keyword.
    Returns dict with url, photographer, photographer_url or None.
    Caches results to avoid hitting rate limits.
    """
    if not UNSPLASH_KEY or UNSPLASH_KEY == "your_key_here":
        return None

    search_term = get_search_term(keyword)
    cache_key   = search_term.replace(" ", "_")[:40]
    cache_file  = CACHE_DIR / f"{cache_key}.json"

    # Check cache (images are cached for 7 days)
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            age    = time.time() - cached.get("cached_at", 0)
            if age < 7 * 86400:
                return cached["image"]
        except Exception:
            pass

    try:
        r = requests.get(
            "https://api.unsplash.com/search/photos",
            params={
                "query":       search_term,
                "per_page":    5,
                "orientation": orientation,
                "content_filter": "high",
            },
            headers={"Authorization": f"Client-ID {UNSPLASH_KEY}"},
            timeout=10,
        )
        r.raise_for_status()
        data    = r.json()
        results = data.get("results", [])

        if not results:
            return None

        # Pick the best result (highest quality, most relevant)
        photo = results[0]
        image = {
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

        # Cache it
        cache_file.write_text(json.dumps({
            "image":     image,
            "cached_at": time.time(),
        }, indent=2))

        return image

    except Exception as e:
        print(f"  [images] Unsplash error for '{search_term}': {e}")
        return None


def get_hero_image(keyword: str) -> dict | None:
    """Get a wide hero/banner image for the top of a post."""
    return fetch_image(keyword, orientation="landscape")


def get_product_image(product_title: str, fallback_keyword: str = "") -> str:
    """
    Get the best available image URL for a product.
    Priority: Amazon product image → Unsplash category image
    Returns image URL string or empty string.
    """
    # If we have a direct Amazon image URL use it
    # (will be populated once PA-API is set up)

    # Fall back to Unsplash
    keyword = product_title or fallback_keyword
    image   = fetch_image(keyword)
    if image:
        return image["url_small"]
    return ""


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
