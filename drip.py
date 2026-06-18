"""
drip.py — Mavrino per-category drip publisher
──────────────────────────────────────────────
Publishes posts spread EVENLY across the day, with a per-main-category daily quota.

  - 6 main categories: Kitchen, Home, Travel, Home Office, Fitness & Wellness, Outdoors.
  - Daily target per category: DRIP_TODAY_TARGET on the catch-up date (default 5),
    DRIP_DAILY_TARGET afterwards (default 2).
  - Run this on a short interval (e.g. every 30 min via cron). Each run publishes only
    the number of posts needed to stay on an even, all-day schedule, and stops once
    every category has hit its daily quota. Picks the most-behind category each time.

State is kept in config/drip_state.json (per-day, per-category counts).
"""

import os, sys, json, time, math
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent / "pipeline"))

import run_pipeline as rp
import cache_builder as cb
import content_generator as cg
import wp_publisher as wp
import taxonomy_manager as tm
import amazon_data as ad
import keyword_discovery as kd
from safe_io import write_json, load_json

MAIN_CATEGORIES = ["Kitchen", "Home", "Travel", "Home Office", "Fitness & Wellness", "Outdoors"]
STATE_FILE = Path("config/drip_state.json")
PER_RUN_CAP = int(os.getenv("DRIP_PER_RUN_CAP", "4"))   # safety cap per invocation
WINDOW_END_HOUR = int(os.getenv("DRIP_WINDOW_END_HOUR", "23"))  # finish the day's quota by ~23:00 UTC

# subcategory (lowercase) -> main vertical
NICHE_TO_VERTICAL = {child.lower(): v for v, kids in tm.CATEGORY_HIERARCHY.items() for child in kids}


def keyword_vertical(keyword: str) -> str | None:
    kw = keyword.lower()
    best, best_len = None, 0
    for niche, vertical in NICHE_TO_VERTICAL.items():
        if niche in kw and len(niche) > best_len:
            best, best_len = vertical, len(niche)
    return best


def _utcnow():
    return datetime.now(timezone.utc)


def load_state() -> dict:
    return load_json(STATE_FILE, {}) or {}


def save_state(s: dict):
    write_json(STATE_FILE, s)


def target_for_today(today: str) -> int:
    # DRIP_CATCHUP_DATE accepts one OR several comma-separated dates. Each listed
    # day publishes the elevated DRIP_TODAY_TARGET; this lets a backlog be spread
    # (back-filled) evenly across multiple catch-up days instead of one burst.
    # Any day not listed uses the normal DRIP_DAILY_TARGET, so the elevation
    # auto-reverts once the listed dates pass — no edit needed.
    catchup_dates = {d.strip() for d in os.getenv("DRIP_CATCHUP_DATE", "").split(",") if d.strip()}
    if today in catchup_dates:
        return int(os.getenv("DRIP_TODAY_TARGET", "5"))
    return int(os.getenv("DRIP_DAILY_TARGET", "2"))


def publish_one(keyword_data: dict):
    keyword   = keyword_data["keyword"]
    post_type = keyword_data.get("post_type", "roundup")
    asins = rp.get_asins_for_keyword(keyword_data)
    products = ad.get_multiple_products(asins) if asins else []
    if not products:
        # The explicit-ASIN path (Amazon PA-API) is unavailable, and many queued
        # comparison/review keywords reference now-delisted ASINs — so this path
        # returns nothing and the post used to silently fail. Fall back to the
        # re-curated niche cache so the post still publishes with live products.
        products = cb.get_products_for_keyword(keyword, count=rp.products_needed(keyword, post_type))
    if not products:
        return None
    content = cg.generate_content(keyword, post_type, products)
    if not content:
        return None
    return wp.publish_to_wordpress(content, products, keyword_data)


def pick_keyword(queue, done, vertical):
    return next((k for k in queue
                 if keyword_vertical(k.get("keyword", "")) == vertical
                 and k.get("slug") not in done), None)


def run():
    now   = _utcnow()
    today = now.strftime("%Y-%m-%d")
    target = target_for_today(today)
    total_target = target * len(MAIN_CATEGORIES)

    print(f"\n{'='*56}\n  Drip — {now:%Y-%m-%d %H:%M} UTC  | target {target}/category\n{'='*56}")

    cb.build_all_caches()

    # Turn zero-result visitor searches into queued guides (no-op until the plugin
    # is installed). Demand-driven content feeds the same quality-gated pipeline.
    try:
        import search_requests
        search_requests.ingest()
    except Exception as e:
        print(f"  [search] ingest skipped: {e}")

    state = load_state()
    day   = state.setdefault(today, {})
    counts = day.setdefault("counts", {v: 0 for v in MAIN_CATEGORIES})
    for v in MAIN_CATEGORIES:
        counts.setdefault(v, 0)
    if "window_start" not in day:
        # Anchor the day's window to 00:00 UTC, NOT to `now`. Anchoring to `now`
        # makes frac==0 on the run that first creates the day's record, so
        # expected==0 and nothing ever publishes — and because the nothing-due
        # path returns without saving, every run re-creates the day at `now`
        # and the drip dead-locks at zero (see 2026-06-16/17).
        day["window_start"] = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    # Even-spacing: how many SHOULD be published by now, across the day's window.
    ws  = datetime.fromisoformat(day["window_start"])
    end = now.replace(hour=WINDOW_END_HOUR, minute=0, second=0, microsecond=0)
    if end <= ws:
        end = ws + timedelta(hours=1)
    frac = max(0.0, min(1.0, (now - ws).total_seconds() / (end - ws).total_seconds()))
    expected = math.ceil(total_target * frac)
    published_today = sum(counts.values())
    to_publish = min(max(0, expected - published_today), PER_RUN_CAP)

    print(f"  published today: {published_today}/{total_target} | on-schedule target now: {expected} "
          f"=> publishing up to {to_publish}")
    if to_publish <= 0:
        print("  nothing due — on schedule.\n" + "=" * 56)
        # Persist the day record (window_start + counts) so the window anchor
        # sticks across the next fresh checkout instead of being re-stamped.
        day["counts"] = counts
        save_state(state)
        return

    queue = rp.load_queue()
    done  = rp.load_done()
    plog  = rp.load_posts_log()
    published = 0

    for _ in range(to_publish):
        gaps = {v: target - counts.get(v, 0) for v in MAIN_CATEGORIES}
        vertical = max(gaps, key=gaps.get)
        if gaps[vertical] <= 0:
            break
        kdata = pick_keyword(queue, done, vertical)
        if not kdata:
            print(f"  [{vertical}] no queued keyword — running discovery…")
            kd.run()
            queue = rp.load_queue()
            kdata = pick_keyword(queue, done, vertical)
        if not kdata:
            print(f"  [{vertical}] still no keyword available — skipping")
            counts[vertical] = target  # don't spin on an empty vertical this run
            continue

        print(f"  [{vertical}] publishing '{kdata['keyword']}'")
        result = publish_one(kdata)
        queue = [k for k in queue if k.get("slug") != kdata.get("slug")]
        if result:
            done.add(kdata.get("slug"))
            counts[vertical] = counts.get(vertical, 0) + 1
            published += 1
            plog.append({
                "date": today, "keyword": kdata["keyword"], "vertical": vertical,
                "post_type": kdata.get("post_type", "roundup"),
                "slug": kdata.get("slug", ""), "title": result.get("title", ""),
                "wp_url": result.get("wp_url", ""),
            })
        rp.save_queue(queue)
        rp.save_done(done)
        rp.save_posts_log(plog)
        day["counts"] = counts
        save_state(state)
        time.sleep(2)

    print(f"\n  published this run: {published} | today total: {sum(counts.values())}/{total_target}")
    print(f"  per-category: {counts}\n" + "=" * 56)


if __name__ == "__main__":
    run()
