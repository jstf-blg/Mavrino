"""
refresh_data.py — Mavrino periodic image & price refresh
─────────────────────────────────────────────────────────
Re-checks every cached product's price, rating, and image on a schedule.

Data source is automatic and self-upgrading:
  • If the Amazon PA-API (or Apify) keys are configured, this pulls LIVE prices
    and images and rewrites the cache — no code change needed when you add keys
    in ~2 months. amazon_data.get_product_data already prefers PA-API → Apify →
    cached fallback, so this script just forces a refresh through that chain.
  • Until then (fallback mode) it validates the cached data and reports dead /
    placeholder product images so you know what will improve once the API is on.

Writes a summary to config/refresh_log.json. Run standalone or on a schedule
(see .github/workflows). Usage:
    python refresh_data.py            # refresh everything
    python refresh_data.py --dry-run  # report only, don't rewrite cache
"""

import os, sys, json, time
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

def _utcnow():
    return datetime.now(timezone.utc)

# UTF-8 console (Windows cp1252 can't encode the symbols we print)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent / "pipeline"))

import amazon_data as ad
import requests

CACHE_DIR = Path("config/product_cache")
LOG_FILE  = Path("config/refresh_log.json")

# A live source exists when real API keys are present (not the .env placeholders)
LIVE_SOURCE = bool(
    (os.getenv("AMAZON_ACCESS_KEY", "") not in ("", "your_amazon_pa_api_access_key"))
    or (os.getenv("APIFY_TOKEN", "") not in ("", "your_apify_token_here"))
)

# Price move (fraction) worth flagging for a post update
PRICE_DELTA_THRESHOLD = 0.05


def _image_reachable(url: str) -> bool:
    if not url:
        return False
    try:
        r = requests.get(url, timeout=10, stream=True)
        ok = r.status_code == 200 and "image" in r.headers.get("Content-Type", "")
        r.close()
        return ok
    except Exception:
        return False


def refresh_all(dry_run: bool = False) -> dict:
    files = sorted(CACHE_DIR.glob("*.json"))
    print(f"\n{'='*56}")
    print(f"  Mavrino Data Refresh — {_utcnow():%Y-%m-%d %H:%M} UTC")
    print(f"  Source: {'LIVE (PA-API/Apify)' if LIVE_SOURCE else 'fallback/seed (no live keys)'}")
    print(f"  Products: {len(files)}  {'(dry run)' if dry_run else ''}")
    print(f"{'='*56}\n")

    summary = {
        "ran_at":          _utcnow().isoformat(),
        "source":          "live" if LIVE_SOURCE else "fallback",
        "products":        len(files),
        "refreshed":       0,
        "price_changes":   [],
        "dead_images":     [],
        "errors":          0,
    }

    for f in files:
        try:
            before = json.loads(f.read_text())
        except Exception:
            summary["errors"] += 1
            continue
        asin      = before.get("asin") or f.stem
        old_price = float(before.get("price", 0) or 0)
        old_img   = before.get("image_url", "")

        # Pull fresh data only when a live source exists; in fallback mode read the
        # cache directly (no futile live fetch attempts).
        if LIVE_SOURCE and not dry_run:
            after = ad.get_product_data(asin, force_refresh=True) or before
        else:
            after = before

        new_price = float(after.get("price", 0) or 0)
        if LIVE_SOURCE and old_price and new_price and \
           abs(new_price - old_price) / old_price >= PRICE_DELTA_THRESHOLD:
            summary["price_changes"].append({
                "asin": asin, "title": after.get("title", "")[:50],
                "old": round(old_price, 2), "new": round(new_price, 2),
            })

        # Image health — flag products whose stored image won't load (so we know
        # which ones still rely on the Unsplash stand-in until real photos land).
        if not _image_reachable(after.get("image_url", "") or old_img):
            summary["dead_images"].append({"asin": asin, "title": after.get("title", "")[:50]})

        summary["refreshed"] += 1
        if summary["refreshed"] % 10 == 0:
            print(f"  …{summary['refreshed']}/{len(files)}")
        time.sleep(0.2)

    # ── Report ────────────────────────────────────────────────────────────────
    print(f"\n  Refreshed:      {summary['refreshed']}")
    print(f"  Price changes:  {len(summary['price_changes'])}")
    for pc in summary["price_changes"][:15]:
        print(f"     {pc['title']:50}  ${pc['old']} → ${pc['new']}")
    print(f"  Dead/placeholder images: {len(summary['dead_images'])}"
          f"{' (expected on fallback — fixed when PA-API lands)' if not LIVE_SOURCE else ''}")
    print(f"  Errors:         {summary['errors']}")

    if not dry_run:
        history = []
        if LOG_FILE.exists():
            try:
                history = json.loads(LOG_FILE.read_text())
            except Exception:
                history = []
        history.append(summary)
        LOG_FILE.write_text(json.dumps(history[-90:], indent=2))
        print(f"\n  Logged to {LOG_FILE}")
    print(f"{'='*56}\n")
    return summary


if __name__ == "__main__":
    refresh_all(dry_run="--dry-run" in sys.argv)
