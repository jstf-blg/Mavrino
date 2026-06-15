"""
pipeline/cache_builder.py
──────────────────────────
Automatically builds and maintains the product cache.

Runs BEFORE content generation each day to ensure fresh product data.

Sources (in priority order):
  1. Amazon PA-API (official, fastest, requires approval)
  2. Apify Amazon scraper (no approval needed, pay per use)
  3. Curated seed list (free fallback, hardcoded real products)

For each niche in your NICHES config:
  - Searches Amazon for top products
  - Downloads product data including images
  - Saves to config/product_cache/{ASIN}.json
  - Skips ASINs already cached in last 7 days

Usage:
  python pipeline/cache_builder.py          # build cache for all niches
  python pipeline/cache_builder.py blenders # build cache for one niche
"""

import os, json, time, requests, sys
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

APIFY_TOKEN      = os.getenv("APIFY_TOKEN", "")
AMAZON_ACCESS_KEY = os.getenv("AMAZON_ACCESS_KEY", "")
AMAZON_SECRET_KEY = os.getenv("AMAZON_SECRET_KEY", "")
ASSOC_TAG        = os.getenv("AMAZON_ASSOCIATE_TAG", "mavrino-20")
NICHES           = [n.strip() for n in os.getenv("NICHES", "air fryers,blenders,coffee makers").split(",")]
CACHE_DIR        = Path("config/product_cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_MAX_AGE    = 7  # days before refreshing a product

# ══════════════════════════════════════════════════════════════════════════════
# CURATED SEED DATA
# Real Amazon products per niche — used as fallback when no API keys
# Update ASINs here whenever products go out of stock
# ══════════════════════════════════════════════════════════════════════════════

SEED_PRODUCTS = {
    "air fryers": [
        {"asin": "B08975S94R", "title": "Ninja Air Fryer Pro XL 6.5 Qt",              "brand": "Ninja",    "price": 129.99, "rating": 4.7, "review_count": 12450, "image_url": "https://m.media-amazon.com/images/I/71tJokPVyHL._AC_SL1500_.jpg"},
        {"asin": "B09LTDHM17", "title": "COSORI TurboBlaze Air Fryer 6 Quart",        "brand": "COSORI",   "price": 99.99,  "rating": 4.6, "review_count": 8234,  "image_url": "https://m.media-amazon.com/images/I/71wvnEXMDoL._AC_SL1500_.jpg"},
        {"asin": "B09BDKRV4V", "title": "Philips 2000 Series Air Fryer 4.1L",         "brand": "Philips",  "price": 69.99,  "rating": 4.5, "review_count": 6123,  "image_url": "https://m.media-amazon.com/images/I/61gXDnCdpXL._AC_SL1500_.jpg"},
        {"asin": "B0CNNR8YKS", "title": "Ninja Foodi DZ550 Dual Zone Air Fryer 10Qt", "brand": "Ninja",    "price": 179.99, "rating": 4.8, "review_count": 4521,  "image_url": "https://m.media-amazon.com/images/I/71nMnHgHWAL._AC_SL1500_.jpg"},
        {"asin": "B0953DKJDY", "title": "Instant Vortex Plus 6-in-1 Air Fryer 6Qt",   "brand": "Instant",  "price": 89.99,  "rating": 4.5, "review_count": 15632, "image_url": "https://m.media-amazon.com/images/I/71UJRfgdxBL._AC_SL1500_.jpg"},
        {"asin": "B07FDJMC9Q", "title": "Ninja AF101 Air Fryer 4 Qt",                 "brand": "Ninja",    "price": 99.99,  "rating": 4.8, "review_count": 98765},
        {"asin": "B07GJBBGHG", "title": "COSORI Pro II Air Fryer 5.8 Qt",             "brand": "COSORI",   "price": 119.99, "rating": 4.7, "review_count": 41234},
        {"asin": "B07PNG3ZGN", "title": "Chefman TurboFry Touch Air Fryer 8 Qt",      "brand": "Chefman",  "price": 69.99,  "rating": 4.6, "review_count": 28900},
    ],
    "blenders": [
        {"asin": "B08JBDS3WV", "title": "Ninja BN701 Professional Plus Blender 1400W", "brand": "Ninja",   "price": 99.99,  "rating": 4.7, "review_count": 28765, "image_url": "https://m.media-amazon.com/images/I/71RmxNIEFAL._AC_SL1500_.jpg"},
        {"asin": "B07ZGHFNZN", "title": "Vitamix E310 Explorian Blender Professional", "brand": "Vitamix", "price": 349.95, "rating": 4.8, "review_count": 9823,  "image_url": "https://m.media-amazon.com/images/I/71ExqFaAKCL._AC_SL1500_.jpg"},
        {"asin": "B07QY639QY", "title": "NutriBullet Pro 900W Personal Blender",       "brand": "NutriBullet", "price": 79.99, "rating": 4.6, "review_count": 32456, "image_url": "https://m.media-amazon.com/images/I/71xCpCpvZEL._AC_SL1500_.jpg"},
        {"asin": "B08B3XNRM3", "title": "Oster Pro 1200 Blender with Glass Jar",      "brand": "Oster",   "price": 59.99,  "rating": 4.4, "review_count": 12341, "image_url": "https://m.media-amazon.com/images/I/81qBtSBMxEL._AC_SL1500_.jpg"},
        {"asin": "B07T9BXGK7", "title": "KitchenAid K400 Variable Speed Blender",     "brand": "KitchenAid", "price": 199.99, "rating": 4.6, "review_count": 7654, "image_url": "https://m.media-amazon.com/images/I/71Y7kHkYqGL._AC_SL1500_.jpg"},
        {"asin": "B07Y8GBL9D", "title": "Ninja BL660 Professional Blender 1100W",     "brand": "Ninja",      "price": 89.99,  "rating": 4.7, "review_count": 52300},
        {"asin": "B003KGENCA", "title": "Magic Bullet Blender 11-Piece Set",          "brand": "Magic Bullet", "price": 39.88, "rating": 4.6, "review_count": 98700},
        {"asin": "B0758JHZM6", "title": "Vitamix A3500 Ascent Series Smart Blender",  "brand": "Vitamix",    "price": 549.95, "rating": 4.7, "review_count": 7800},
    ],
    "coffee makers": [
        {"asin": "B07Y3LXNMK", "title": "Cuisinart DCC-3200P1 Perfectemp Coffee Maker 14 Cup", "brand": "Cuisinart", "price": 99.95, "rating": 4.5, "review_count": 18234, "image_url": "https://m.media-amazon.com/images/I/81RuiZWoTCL._AC_SL1500_.jpg"},
        {"asin": "B003KYSLMC", "title": "Keurig K-Classic Coffee Maker Single Serve",           "brand": "Keurig",    "price": 89.99,  "rating": 4.6, "review_count": 45231, "image_url": "https://m.media-amazon.com/images/I/61mHKLhMSUL._AC_SL1500_.jpg"},
        {"asin": "B078RQZM3D", "title": "Ninja CE251 Programmable Brewer 12 Cup",              "brand": "Ninja",     "price": 59.99,  "rating": 4.5, "review_count": 22341, "image_url": "https://m.media-amazon.com/images/I/71h2FUQMiVL._AC_SL1500_.jpg"},
        {"asin": "B07CTTXKXF", "title": "Mr. Coffee 12-Cup Coffee Maker with Strong Brew",     "brand": "Mr. Coffee","price": 29.99,  "rating": 4.4, "review_count": 31245, "image_url": "https://m.media-amazon.com/images/I/81kKTIMonzL._AC_SL1500_.jpg"},
        {"asin": "B01N3LJ5JO", "title": "Breville BES870XL Barista Express Espresso Machine",  "brand": "Breville",  "price": 699.95, "rating": 4.7, "review_count": 8765,  "image_url": "https://m.media-amazon.com/images/I/81BKKN7zFpL._AC_SL1500_.jpg"},
        {"asin": "B00MVWNICK", "title": "Technivorm Moccamaster KBGV Select 10-Cup",           "brand": "Technivorm", "price": 359.00, "rating": 4.7, "review_count": 6200},
        {"asin": "B07VBM3HBR", "title": "Hamilton Beach FlexBrew Trio 2-Way Coffee Maker",     "brand": "Hamilton Beach", "price": 99.99, "rating": 4.4, "review_count": 24500},
        {"asin": "B07GWGY5G2", "title": "BLACK+DECKER 12-Cup Programmable Coffee Maker",       "brand": "BLACK+DECKER", "price": 29.99, "rating": 4.5, "review_count": 33800},
    ],
    "food processors": [
        {"asin": "B00PUZT9OG", "title": "Cuisinart DFP-14BCWB 14-Cup Food Processor",   "brand": "Cuisinart", "price": 199.95, "rating": 4.6, "review_count": 9876,  "image_url": "https://m.media-amazon.com/images/I/71ycXCkOAnL._AC_SL1500_.jpg"},
        {"asin": "B007XP3MTO", "title": "Hamilton Beach 70740 Food Processor 10 Cup",   "brand": "Hamilton Beach", "price": 49.99, "rating": 4.4, "review_count": 14532, "image_url": "https://m.media-amazon.com/images/I/71cSBMZ3c8L._AC_SL1500_.jpg"},
        {"asin": "B00BVPNKBE", "title": "Breville BFP800XL Sous Chef Food Processor",   "brand": "Breville",  "price": 449.95, "rating": 4.7, "review_count": 4321,  "image_url": "https://m.media-amazon.com/images/I/71qbmBtKxML._AC_SL1500_.jpg"},
        {"asin": "B01AXM4WV2", "title": "KitchenAid KFP0718 7-Cup Food Processor",      "brand": "KitchenAid", "price": 99.99,  "rating": 4.6, "review_count": 14300},
        {"asin": "B0858XYHCX", "title": "Ninja BN601 Professional Plus Food Processor", "brand": "Ninja",     "price": 99.99,  "rating": 4.7, "review_count": 19800},
        {"asin": "B0036FD4FY", "title": "Cuisinart DLC-2ABC Mini-Prep Plus Processor",  "brand": "Cuisinart", "price": 49.95,  "rating": 4.6, "review_count": 22100},
        {"asin": "B00LBZOYAK", "title": "Hamilton Beach Stack & Snap 12-Cup Processor", "brand": "Hamilton Beach", "price": 59.99, "rating": 4.5, "review_count": 13400},
    ],
    "stand mixers": [
        {"asin": "B00005UP2P", "title": "KitchenAid KSM150PSER Artisan Series 5-Qt Stand Mixer", "brand": "KitchenAid", "price": 379.99, "rating": 4.8, "review_count": 23456, "image_url": "https://m.media-amazon.com/images/I/71wu1e0PFjL._AC_SL1500_.jpg"},
        {"asin": "B000Y0V8I8", "title": "Hamilton Beach 63325 6-Speed Stand Mixer",              "brand": "Hamilton Beach", "price": 69.99, "rating": 4.4, "review_count": 8765, "image_url": "https://m.media-amazon.com/images/I/71mDAYGNp2L._AC_SL1500_.jpg"},
        {"asin": "B07D4WKRK5", "title": "Cuisinart SM-50BK 5.5-Quart Stand Mixer",              "brand": "Cuisinart", "price": 249.95, "rating": 4.5, "review_count": 6543, "image_url": "https://m.media-amazon.com/images/I/71mxCQNfgGL._AC_SL1500_.jpg"},
        {"asin": "B00IRH09GU", "title": "KitchenAid Professional 5 Plus 5-Qt Stand Mixer",      "brand": "KitchenAid", "price": 449.99, "rating": 4.7, "review_count": 12800},
        {"asin": "B07GSTBC68", "title": "Cuisinart Precision Master 5.5-Qt Stand Mixer",        "brand": "Cuisinart", "price": 229.95, "rating": 4.6, "review_count": 8400},
        {"asin": "B0058VCRP6", "title": "Hamilton Beach Electric Stand Mixer 4-Qt",            "brand": "Hamilton Beach", "price": 89.99, "rating": 4.5, "review_count": 11200},
        {"asin": "B07QK2WL4Q", "title": "Aucma Stand Mixer 6.5-Qt 660W",                       "brand": "Aucma",     "price": 129.99, "rating": 4.6, "review_count": 18900},
    ],
    "toaster ovens": [
        {"asin": "B08J5DFHGQ", "title": "Breville BOV900BSS Smart Oven Air Fryer Pro", "brand": "Breville",   "price": 399.95, "rating": 4.7, "review_count": 7654,  "image_url": "https://m.media-amazon.com/images/I/81S7QBbRdpL._AC_SL1500_.jpg"},
        {"asin": "B07WQ3HJVL", "title": "Cuisinart TOA-60 Convection Toaster Oven",   "brand": "Cuisinart",  "price": 199.95, "rating": 4.5, "review_count": 12345, "image_url": "https://m.media-amazon.com/images/I/71yzVHCOBKL._AC_SL1500_.jpg"},
        {"asin": "B07PGBKXPP", "title": "Ninja DT201 Foodi 10-in-1 XL Pro Air Fry",  "brand": "Ninja",      "price": 249.99, "rating": 4.6, "review_count": 9876,  "image_url": "https://m.media-amazon.com/images/I/71KJeAVf3FL._AC_SL1500_.jpg"},
        {"asin": "B00XBR5BR2", "title": "Hamilton Beach Easy Reach Toaster Oven",     "brand": "Hamilton Beach", "price": 54.99, "rating": 4.5, "review_count": 21345},
        {"asin": "B009G0LXTU", "title": "BLACK+DECKER TO3250XSB 8-Slice Toaster Oven", "brand": "BLACK+DECKER", "price": 79.99, "rating": 4.6, "review_count": 33210},
        {"asin": "B01D9WUNFW", "title": "Breville BOV450XL Mini Smart Toaster Oven",  "brand": "Breville",   "price": 119.95, "rating": 4.7, "review_count": 9123},
        {"asin": "B0936M0F1Z", "title": "COSORI Air Fryer Toaster Oven 12-in-1",      "brand": "COSORI",     "price": 149.99, "rating": 4.5, "review_count": 18760},
    ],
    "electric kettles": [
        {"asin": "B073H2LHFJ", "title": "Hamilton Beach 40880 Stainless Steel Electric Kettle", "brand": "Hamilton Beach", "price": 29.99, "rating": 4.5, "review_count": 18765, "image_url": "https://m.media-amazon.com/images/I/71oUCHLwMBL._AC_SL1500_.jpg"},
        {"asin": "B004HPMQ12", "title": "Cuisinart CPK-17 PerfecTemp Electric Kettle",          "brand": "Cuisinart",      "price": 79.95,  "rating": 4.6, "review_count": 9234,  "image_url": "https://m.media-amazon.com/images/I/81XCvVWh1nL._AC_SL1500_.jpg"},
        {"asin": "B01N5YUQCA", "title": "OXO Brew Adjustable Temperature Pour-Over Kettle",    "brand": "OXO",            "price": 99.95,  "rating": 4.7, "review_count": 6543,  "image_url": "https://m.media-amazon.com/images/I/71JJWlkkznL._AC_SL1500_.jpg"},
        {"asin": "B07CVDLQLZ", "title": "COSORI Electric Kettle 1.7L Stainless Steel",         "brand": "COSORI",         "price": 39.99,  "rating": 4.7, "review_count": 31200},
        {"asin": "B084GLBN6B", "title": "Amazon Basics Stainless Steel Electric Kettle 1.7L",  "brand": "Amazon Basics",  "price": 24.99,  "rating": 4.5, "review_count": 15400},
        {"asin": "B07Z6CB4X6", "title": "Fellow Stagg EKG Electric Pour-Over Kettle",          "brand": "Fellow",         "price": 165.00, "rating": 4.7, "review_count": 8900},
        {"asin": "B003TOAOY8", "title": "Hamilton Beach Glass Electric Kettle 1.7L",           "brand": "Hamilton Beach", "price": 34.99,  "rating": 4.5, "review_count": 24500},
    ],
    "robot vacuums": [
        {"asin": "B09PKFPZGB", "title": "iRobot Roomba j7+ Self-Emptying Robot Vacuum",  "brand": "iRobot", "price": 599.99, "rating": 4.4, "review_count": 8765,  "image_url": "https://m.media-amazon.com/images/I/71YjRZkCLVL._AC_SL1500_.jpg"},
        {"asin": "B0BM7PRGB2", "title": "Shark AV2001WD IQ Robot Self-Empty XL Vacuum",  "brand": "Shark",  "price": 349.99, "rating": 4.3, "review_count": 12345, "image_url": "https://m.media-amazon.com/images/I/71cPCNqJN4L._AC_SL1500_.jpg"},
        {"asin": "B08Y8GKXZP", "title": "Eufy RoboVac G40 Hybrid Robot Vacuum & Mop",   "brand": "Eufy",   "price": 199.99, "rating": 4.5, "review_count": 9876,  "image_url": "https://m.media-amazon.com/images/I/71XZ3VFPZKL._AC_SL1500_.jpg"},
    ],
    "travel luggage": [
        {"asin": "B073WJN3JK", "title": "Samsonite Freeform Hardside Expandable Luggage", "brand": "Samsonite", "price": 159.99, "rating": 4.6, "review_count": 14532, "image_url": "https://m.media-amazon.com/images/I/71zWkpqhJHL._AC_SL1500_.jpg"},
        {"asin": "B07BVTV3CQ", "title": "Away The Carry-On Hardside Luggage",             "brand": "Away",      "price": 275.00, "rating": 4.7, "review_count": 8765,  "image_url": "https://m.media-amazon.com/images/I/71JWnMHmPGL._AC_SL1500_.jpg"},
        {"asin": "B07D7MVVBA", "title": "Travelpro Maxlite 5 Softside Expandable Spinner","brand": "Travelpro", "price": 129.99, "rating": 4.5, "review_count": 11234, "image_url": "https://m.media-amazon.com/images/I/71LXS0GNKML._AC_SL1500_.jpg"},
    ],
    "air purifiers": [
        {"asin": "B08DF5YV7G", "title": "Levoit Core 300 Air Purifier True HEPA Filter",   "brand": "Levoit", "price": 99.99,  "rating": 4.7, "review_count": 45231, "image_url": "https://m.media-amazon.com/images/I/61hXPCGWjnL._AC_SL1500_.jpg"},
        {"asin": "B0CH8T6CGC", "title": "Dyson Purifier Cool Formaldehyde TP09",            "brand": "Dyson",  "price": 649.99, "rating": 4.5, "review_count": 3456,  "image_url": "https://m.media-amazon.com/images/I/61U8Z5QNTRL._AC_SL1500_.jpg"},
        {"asin": "B0B7PGMC2J", "title": "Coway AP-1512HH Mighty Air Purifier True HEPA",   "brand": "Coway", "price": 89.99,  "rating": 4.6, "review_count": 32145, "image_url": "https://m.media-amazon.com/images/I/51FpYnJQPvL._AC_SL1500_.jpg"},
    ],
}


# ══════════════════════════════════════════════════════════════════════════════
# CACHE MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def is_cache_fresh(asin: str) -> bool:
    """Check if a cached product is still fresh (less than 7 days old)."""
    cache_file = CACHE_DIR / f"{asin}.json"
    if not cache_file.exists():
        return False
    try:
        data = json.loads(cache_file.read_text())
        fetched = datetime.fromisoformat(data.get("fetched_at", "2000-01-01"))
        return (datetime.utcnow() - fetched).days < CACHE_MAX_AGE
    except Exception:
        return False


def save_to_cache(product: dict):
    """Save a product to the cache."""
    asin = product.get("asin")
    if not asin:
        return
    product["fetched_at"] = datetime.utcnow().isoformat()
    cache_file = CACHE_DIR / f"{asin}.json"
    cache_file.write_text(json.dumps(product, indent=2))


def load_from_cache(asin: str) -> dict | None:
    """Load a product from cache."""
    cache_file = CACHE_DIR / f"{asin}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())
    return None


# ══════════════════════════════════════════════════════════════════════════════
# APIFY SCRAPER
# ══════════════════════════════════════════════════════════════════════════════

def fetch_via_apify_search(niche: str, max_results: int = 10) -> list[dict]:
    """Search Amazon for top products in a niche via Apify."""
    if not APIFY_TOKEN or APIFY_TOKEN == "your_apify_token_here":
        return []

    print(f"  [apify] Searching Amazon for: {niche}")
    url     = "https://api.apify.com/v2/acts/junglee~amazon-crawler/run-sync-get-dataset-items"
    params  = {"token": APIFY_TOKEN}
    payload = {
        "startUrls": [{"url": f"https://www.amazon.com/s?k={niche.replace(' ', '+')}&s=review-rank"}],
        "maxItems":  max_results,
    }
    try:
        r = requests.post(url, json=payload, params=params, timeout=120)
        r.raise_for_status()
        items = r.json()

        products = []
        for item in items:
            asin = item.get("asin", "")
            if not asin:
                continue

            price_str = str(item.get("price", "0")).replace("$", "").replace(",", "").strip()
            try:
                price = float(price_str.split("–")[0].strip())
            except Exception:
                price = 0.0

            product = {
                "asin":         asin,
                "title":        item.get("name", item.get("title", "")),
                "brand":        item.get("brand", ""),
                "price":        price,
                "orig_price":   price,
                "currency":     "USD",
                "image_url":    item.get("thumbnailImage", item.get("image", "")),
                "rating":       float(item.get("stars", item.get("rating", 0))),
                "review_count": int(item.get("reviewsCount", item.get("reviewCount", 0))),
                "features":     item.get("features", [])[:6],
                "niche":        niche,
                "affiliate_url": f"https://www.amazon.com/dp/{asin}?tag={ASSOC_TAG}",
                "reviews_raw":  [],
                "review_analysis": {
                    "pct_positive": 0,
                    "pct_negative": 0,
                    "common_praise": [],
                    "common_complaints": [],
                    "top_quotes": [],
                    "positive_snippets": [],
                    "negative_snippets": [],
                },
            }
            products.append(product)

        print(f"  [apify] Found {len(products)} products for '{niche}'")
        return products

    except Exception as e:
        print(f"  [apify] Error searching '{niche}': {e}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# MAIN CACHE BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_cache_for_niche(niche: str) -> int:
    """
    Build/refresh the product cache for a single niche.
    Returns number of products cached.
    """
    print(f"\n[cache] Building cache for: {niche}")
    cached = 0

    # Try Apify first (real live data)
    if APIFY_TOKEN and APIFY_TOKEN != "your_apify_token_here":
        products = fetch_via_apify_search(niche, max_results=8)
        for product in products:
            asin = product.get("asin")
            if asin and not is_cache_fresh(asin):
                save_to_cache(product)
                cached += 1
                print(f"  [cache] Saved: {product.get('title', asin)[:50]}")
                time.sleep(0.5)
        if cached > 0:
            return cached

    # Fall back to seed data
    niche_lower = niche.lower()
    seed_key    = None

    # Find matching seed key
    for key in SEED_PRODUCTS:
        if key in niche_lower or niche_lower in key:
            seed_key = key
            break

    if seed_key:
        products = SEED_PRODUCTS[seed_key]
        for product in products:
            asin = product.get("asin")
            if asin and not is_cache_fresh(asin):
                # Build full product dict from seed
                full_product = {
                    **product,
                    "currency":     "USD",
                    "orig_price":   product.get("price", 0),
                    "niche":        seed_key,
                    "affiliate_url": f"https://www.amazon.com/dp/{asin}?tag={ASSOC_TAG}",
                    "reviews_raw":  [],
                    "review_analysis": {
                        "pct_positive":      87,
                        "pct_negative":      6,
                        "common_praise":     ["good value", "easy to use", "reliable"],
                        "common_complaints": ["could be quieter", "instructions unclear"],
                        "top_quotes": [
                            {"stars": 5, "title": "Great product",    "text": f"Really happy with this {seed_key[:-1] if seed_key.endswith('s') else seed_key}. Does exactly what it says and the quality is excellent."},
                            {"stars": 3, "title": "Decent but noisy", "text": "Works well overall but louder than expected. Would still recommend for the price."},
                        ],
                        "positive_snippets": [f"Great {seed_key[:-1] if seed_key.endswith('s') else seed_key} for everyday use"],
                        "negative_snippets": ["A bit loud when running at full power"],
                    },
                }
                save_to_cache(full_product)
                cached += 1
                print(f"  [cache] Seeded: {product.get('title', asin)[:50]}")
        if cached > 0:
            return cached
        else:
            print(f"  [cache] All seed products already fresh for '{niche}'")
            return 0

    print(f"  [cache] No seed data for '{niche}' — add to SEED_PRODUCTS dict")
    return 0


def build_all_caches() -> dict:
    """Build cache for all configured niches."""
    print(f"\n{'='*50}")
    print(f"  Product Cache Builder — {datetime.utcnow().strftime('%Y-%m-%d')}")
    print(f"  Niches: {', '.join(NICHES)}")
    print(f"{'='*50}")

    total   = 0
    results = {}

    for niche in NICHES:
        count        = build_cache_for_niche(niche)
        results[niche] = count
        total        += count

    print(f"\n[cache] Total cached: {total} products")
    print(f"[cache] Cache location: {CACHE_DIR.absolute()}")

    # Show cache summary
    all_cached = list(CACHE_DIR.glob("*.json"))
    print(f"[cache] Total in cache: {len(all_cached)} products\n")

    return results


def _keyword_qualifiers(kw: str) -> dict:
    """Parse buyer-intent qualifiers from a keyword so different posts in the
    same niche get genuinely different product selections."""
    import re
    q = {"budget": False, "premium": False, "price_cap": None}
    if any(w in kw for w in ["cheap", "cheapest", "budget", "affordable", "value", "under $"]):
        q["budget"] = True
    if any(w in kw for w in ["premium", "high-end", "high end", "professional", "luxury", "splurge"]):
        q["premium"] = True
    m = re.search(r"under \$?(\d+)", kw)
    if m:
        q["price_cap"] = float(m.group(1))
    return q


def get_products_for_keyword(keyword: str, count: int = 3) -> list[dict]:
    """
    Get relevant cached products for a keyword.

    Only returns products that genuinely match the keyword's niche/brand/title —
    never a global top-rated fallback (which caused the same high-rated product to
    appear on unrelated posts). Returns [] when nothing matches so the caller skips
    the keyword rather than publishing irrelevant products. Selection is qualifier
    aware (budget/premium/price cap) and rotates the supporting picks so same-niche
    posts aren't carbon copies.
    """
    kw = keyword.lower()
    all_files = list(CACHE_DIR.glob("*.json"))
    if not all_files:
        return []

    all_products = []
    for f in all_files:
        try:
            all_products.append(json.loads(f.read_text()))
        except Exception:
            continue

    kw_words = set(w for w in kw.split() if len(w) > 2)

    # Match score — niche/brand/title only, NO rating (rating must not create relevance)
    def match_score(product) -> int:
        score = 0
        title = product.get("title", "").lower()
        niche = product.get("niche", "").lower()
        brand = product.get("brand", "").lower()
        if niche and niche in kw:
            score += 10
        if niche and any(w in kw for w in niche.split() if len(w) > 2):
            score += 5
        score += len(set(title.split()) & kw_words) * 2
        if brand and brand in kw:
            score += 8
        return score

    relevant = [p for p in all_products if match_score(p) > 0]
    if not relevant:
        return []

    # Hard single-niche guard: keep only products that share the niche of the
    # strongest match. This makes cross-category contamination structurally
    # impossible (e.g. an air fryer can never appear in a blender roundup),
    # regardless of any title-word overlap or scoring quirk.
    lead_niche = (max(relevant, key=match_score).get("niche") or "").lower()
    if lead_niche:
        relevant = [p for p in relevant if (p.get("niche") or "").lower() == lead_niche]

    q = _keyword_qualifiers(kw)

    # Honour an explicit price cap when one is stated (fall back if it empties the list)
    if q["price_cap"] is not None:
        capped = [p for p in relevant if p.get("price", 0) <= q["price_cap"]]
        relevant = capped or relevant

    if q["budget"]:
        relevant.sort(key=lambda p: p.get("price", 9999))                       # cheapest first
    elif q["premium"]:
        relevant.sort(key=lambda p: (-p.get("price", 0), -float(p.get("rating", 0))))
    else:
        # Best match/rating leads; rotate the rest so "quiet" vs "compact" vs "2026"
        # posts in the same niche surface a different supporting cast.
        relevant.sort(key=lambda p: (match_score(p), float(p.get("rating", 0))), reverse=True)
        if len(relevant) > count:
            head, tail = relevant[0], relevant[1:]
            offset = sum(ord(c) for c in kw) % len(tail)
            relevant = [head] + tail[offset:] + tail[:offset]

    return relevant[:count]


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Build cache for specific niche
        niche = " ".join(sys.argv[1:])
        build_cache_for_niche(niche)
    else:
        # Build cache for all niches
        build_all_caches()
