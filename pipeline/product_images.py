"""
pipeline/product_images.py
──────────────────────────
Self-healing product-image + ASIN validator.

The seed catalogue ships hardcoded Amazon image URLs that go stale (Amazon
rotates image hashes) and some seed ASINs are delisted entirely. Both ship as
silent 404s: dead product photos and affiliate links to products that no longer
exist. This module fixes that automatically.

For each ASIN it fetches the public product page (https://www.amazon.com/dp/ASIN)
and extracts the real current main image (og:image / hiRes / landingImage), then
validates the image actually loads. Results are cached in config/product_images.json
with TTLs so the network work happens at most weekly per ASIN.

Design for running in CI (where Amazon may rate-limit datacenter IPs):
  - A *blocked* response (503/429/captcha) NEVER erases a good cached image and
    NEVER counts toward the dead-ASIN tally — it just retries next run.
  - An ASIN is only flagged ``dead_asin`` after DEAD_CONFIRM consecutive clean
    404/410 responses, so a transient block can't false-flag a live product.
  - Nothing is destructively deleted. Dead ASINs are *excluded* at product
    selection (reversible the moment the ASIN resolves again).

Public API (cache-read only, no network — safe to call from the hot path):
  get_image_url(asin)  -> live image URL string, or "" if none known good
  is_dead_asin(asin)   -> True only for confirmed-delisted ASINs

Network entry point (used by the daily product_health workflow):
  run_health_check()   -> resolve stale ASINs, patch the product cache, report

CLI:
  python pipeline/product_images.py --health        # validate/refresh all known ASINs
  python pipeline/product_images.py B07FDJMC9Q ...  # check specific ASINs (force)
"""

import os, sys, json, time, hashlib, re
from pathlib import Path
from datetime import datetime, timezone

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# safe_io lives alongside this module; import works both as `pipeline.product_images`
# (cache_builder adds pipeline/ to sys.path) and when run directly.
try:
    from safe_io import load_json, write_json
except Exception:  # pragma: no cover - fallback when imported as a package
    from pipeline.safe_io import load_json, write_json

CACHE_FILE   = Path("config/product_images.json")
PRODUCT_CACHE_DIR = Path("config/product_cache")

OK_TTL_DAYS   = 7     # re-validate a good image weekly
DEAD_TTL_DAYS = 14    # re-check a confirmed-dead ASIN biweekly (it may relist)
DEAD_CONFIRM  = 3     # consecutive clean 404s before flagging an ASIN dead
REQUEST_GAP   = 1.5   # polite delay (s) between live Amazon page fetches
MIN_PAGE_BYTES = 60000  # a 200 body smaller than this is a throttle/interstitial stub

# Rotated so a batch doesn't hammer with one fingerprint. Picked deterministically
# by ASIN hash (no RNG) so results are reproducible.
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

# Markers Amazon serves on a bot-block / captcha interstitial (HTTP 200 body).
_BLOCK_MARKERS = (
    "Robot Check",
    "Enter the characters you see below",
    "api-services-support@amazon.com",
    "To discuss automated access",
    "automated access to Amazon data",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _load() -> dict:
    return load_json(CACHE_FILE, {}) or {}


def _save(cache: dict) -> None:
    write_json(CACHE_FILE, cache)


def _headers(asin: str) -> dict:
    ua = _USER_AGENTS[int(hashlib.md5(asin.encode()).hexdigest(), 16) % len(_USER_AGENTS)]
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    }


def _extract_image(html: str) -> str | None:
    """Pull the best main product image URL from a product page's HTML."""
    patterns = (
        r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"',
        r'"hiRes":"(https://[^"]+?\.jpg)"',
        r'"large":"(https://[^"]+?\.jpg)"',
        r'id="landingImage"[^>]+src="([^"]+)"',
        r'"mainUrl":"(https://[^"]+?\.jpg)"',
    )
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            url = m.group(1).replace("\\/", "/")
            if url.startswith("http") and "media-amazon" in url:
                return url
    return None


def _validate_image(url: str) -> bool:
    """Confirm the image URL actually returns image bytes (cheap HEAD)."""
    try:
        r = requests.head(url, timeout=15, allow_redirects=True)
        if r.status_code != 200:
            # Some CDNs reject HEAD; fall back to a tiny ranged GET.
            r = requests.get(url, timeout=15, headers={"Range": "bytes=0-1023"})
        return r.status_code in (200, 206) and "image" in r.headers.get("Content-Type", "")
    except Exception:
        return False


def _classify(asin: str) -> dict:
    """Make one live attempt. Returns {status, image_url, http_status}.

    status ∈ ok | dead | no_image | blocked | error  (note: 'dead' here is a
    single 404 observation; escalation to the sticky 'dead_asin' state happens
    in resolve_image once DEAD_CONFIRM consecutive deads accrue).
    """
    url = f"https://www.amazon.com/dp/{asin}"
    try:
        r = requests.get(url, headers=_headers(asin), timeout=25)
    except Exception as e:
        return {"status": "error", "image_url": "", "http_status": 0, "note": str(e)[:120]}

    if r.status_code in (404, 410):
        return {"status": "dead", "image_url": "", "http_status": r.status_code}
    if r.status_code in (429, 503) or any(m in r.text for m in _BLOCK_MARKERS):
        return {"status": "blocked", "image_url": "", "http_status": r.status_code}
    if r.status_code != 200:
        return {"status": "error", "image_url": "", "http_status": r.status_code}
    # A real product page is large (~hundreds of KB+). A tiny 200 body is Amazon's
    # rate-limit/interstitial stub — treat as transient 'blocked', never as dead or
    # no_image, so CI throttling can't corrupt the cache or erase a good image.
    if len(r.text) < MIN_PAGE_BYTES:
        return {"status": "blocked", "image_url": "", "http_status": 200, "note": f"stub {len(r.text)}B"}

    img = _extract_image(r.text)
    if not img:
        return {"status": "no_image", "image_url": "", "http_status": 200}
    if not _validate_image(img):
        return {"status": "no_image", "image_url": "", "http_status": 200, "note": "image 404"}
    return {"status": "ok", "image_url": img, "http_status": 200}


def _fresh(entry: dict) -> bool:
    """Should we SKIP a live re-check for this cached entry?"""
    try:
        age_days = (_now() - datetime.fromisoformat(entry["checked_at"])).days
    except Exception:
        return False
    status = entry.get("status")
    if status == "ok":
        return age_days < OK_TTL_DAYS
    if status == "dead_asin":
        return age_days < DEAD_TTL_DAYS
    return False  # tentative / blocked / error → always retry


def resolve_image(asin: str, cache: dict | None = None, force: bool = False) -> dict:
    """Resolve (and cache) the live image + liveness for one ASIN.

    Returns the cache entry. Performs a network call only when the cached entry
    is stale/missing (unless ``force``). Pass a shared ``cache`` dict in a batch
    to avoid re-reading the file per ASIN; the caller persists it.
    """
    own = cache is None
    if cache is None:
        cache = _load()
    entry = cache.get(asin, {})

    if not force and entry and _fresh(entry):
        return entry

    res = _classify(asin)
    now = _iso(_now())
    new = dict(entry)
    new["checked_at"] = now
    new["http_status"] = res.get("http_status", 0)
    if res.get("note"):
        new["note"] = res["note"]

    if res["status"] == "ok":
        new.update(status="ok", image_url=res["image_url"], fail_count=0, last_ok_at=now)
    elif res["status"] == "dead":
        fc = int(entry.get("fail_count", 0)) + 1
        new["fail_count"] = fc
        if fc >= DEAD_CONFIRM:
            new.update(status="dead_asin", image_url="")
        else:
            # tentative — keep any prior good image until confirmed
            new.setdefault("status", "tentative_dead")
            if entry.get("status") != "ok":
                new["status"] = "tentative_dead"
    elif res["status"] == "no_image":
        # Page is live but no usable image; don't touch dead tally or a prior good url.
        if entry.get("status") != "ok":
            new["status"] = "no_image"
    else:  # blocked / error — transient: never escalate, never erase a good image
        new.setdefault("status", res["status"])
        new["last_block_at"] = now

    cache[asin] = new
    if own:
        _save(cache)
    return new


# ── Cache-read helpers (no network — safe on the publishing hot path) ──────────

def get_image_url(asin: str, cache: dict | None = None) -> str:
    if not asin:
        return ""
    entry = (cache or _load()).get(asin, {})
    return entry.get("image_url", "") if entry.get("status") == "ok" else ""


def is_dead_asin(asin: str, cache: dict | None = None) -> bool:
    if not asin:
        return False
    return (cache or _load()).get(asin, {}).get("status") == "dead_asin"


# ── Automatic health check (network) — run by the scheduled workflow ───────────

def _known_asins() -> list[str]:
    """Every ASIN we might publish: cached products + seed catalogue."""
    asins = {f.stem for f in PRODUCT_CACHE_DIR.glob("*.json")} if PRODUCT_CACHE_DIR.exists() else set()
    try:
        import cache_builder as cb
        for products in cb.SEED_PRODUCTS.values():
            for p in products:
                if p.get("asin"):
                    asins.add(p["asin"])
    except Exception as e:
        print(f"  [img-health] seed enumerate skipped: {e}")
    return sorted(asins)


def _patch_product_cache(asin: str, image_url: str) -> bool:
    """Write a freshly-resolved live image straight into the product cache so
    published posts use it immediately (instead of waiting for the 7-day rebuild)."""
    f = PRODUCT_CACHE_DIR / f"{asin}.json"
    if not f.exists():
        return False
    data = load_json(f, None)
    if not isinstance(data, dict) or data.get("image_url") == image_url:
        return False
    data["image_url"] = image_url
    write_json(f, data)
    return True


def run_health_check(limit: int | None = None, force: bool = False) -> dict:
    """Validate/refresh every known ASIN, patch the product cache, and report.

    Network calls are made only for stale/missing entries (unless ``force``);
    cache hits are free, so the daily run settles into near-zero traffic.
    """
    asins = _known_asins()
    if limit:
        asins = asins[:limit]
    cache = _load()
    stats = {"total": len(asins), "ok": 0, "dead": 0, "tentative": 0,
             "no_image": 0, "blocked": 0, "error": 0, "skipped": 0, "patched": 0}
    newly_dead = []

    print(f"\n{'='*56}\n  Product image health — {_now():%Y-%m-%d %H:%M} UTC | {len(asins)} ASINs\n{'='*56}")
    for i, asin in enumerate(asins, 1):
        before = cache.get(asin, {}).copy()
        was_fresh = bool(before) and not force and _fresh(before)
        entry = resolve_image(asin, cache=cache, force=force)
        if was_fresh:
            stats["skipped"] += 1
        else:
            time.sleep(REQUEST_GAP)  # only pause when we actually hit the network
        if i % 25 == 0:
            _save(cache)  # checkpoint so an interrupted/timed-out run keeps its progress

        status = entry.get("status")
        if status == "ok":
            stats["ok"] += 1
            if _patch_product_cache(asin, entry["image_url"]):
                stats["patched"] += 1
        elif status == "dead_asin":
            stats["dead"] += 1
            if before.get("status") != "dead_asin":
                newly_dead.append(asin)
        elif status == "tentative_dead":
            stats["tentative"] += 1
        elif status == "no_image":
            stats["no_image"] += 1
        elif status == "blocked":
            stats["blocked"] += 1
        else:
            stats["error"] += 1

    _save(cache)

    print(f"\n  ok={stats['ok']}  dead={stats['dead']}  tentative={stats['tentative']}  "
          f"no_image={stats['no_image']}  blocked={stats['blocked']}  error={stats['error']}  "
          f"skipped={stats['skipped']}")
    print(f"  product-cache images patched: {stats['patched']}")
    if newly_dead:
        print(f"  ⚠ newly-confirmed dead ASINs (excluded from posts): {', '.join(newly_dead)}")
    if stats["blocked"] > stats["ok"] and stats["blocked"] > 3:
        print("  ⚠ many fetches blocked — likely datacenter rate-limiting; good images kept, will retry next run.")
    print("=" * 56)
    return stats


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    if "--health" in args:
        force = "--force" in args
        rest = [a for a in args if a not in ("--health", "--force")]
        lim = int(rest[0]) if rest and rest[0].isdigit() else None
        run_health_check(limit=lim, force=force)
    elif args:
        c = _load()
        for a in args:
            print(a, "→", resolve_image(a, cache=c, force=True))
        _save(c)
    else:
        print(__doc__)
