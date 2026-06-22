"""pipeline/post_updater.py — re-run published posts through the upgraded generator.

Refetches each logged post, regenerates its content with the new confidence-scoring +
bias-correction pipeline, and updates the WordPress post in place (same URL/status).
Runs in sub-batches of 10 with 30s pauses to respect WordPress rate limits, and rotates
through the back-catalogue oldest-first so a weekly run gradually refreshes everything.

Usage:
    python pipeline/post_updater.py --dry-run            # show the plan, change nothing
    python pipeline/post_updater.py --batch-size 50      # refresh up to 50 posts
"""
import os
import sys
import json
import time
import argparse
import datetime

import requests

# Make sibling pipeline modules importable regardless of the working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

import wp_publisher as wp
import content_generator as cg
import cache_builder as cb

ROOT        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLISH_LOG = os.path.join(ROOT, "logs", "wp_publish_log.json")
UPDATE_LOG  = os.path.join(ROOT, "logs", "post_update_log.json")

SUB_BATCH       = 10   # process this many, then pause
SUB_BATCH_PAUSE = 30   # seconds between sub-batches (WordPress rate-limit courtesy)


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_update_log(entries):
    os.makedirs(os.path.dirname(UPDATE_LOG), exist_ok=True)
    with open(UPDATE_LOG, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)


def select_posts(publish_log, update_log, batch_size):
    """Oldest-first rotation: posts never refreshed (or refreshed longest ago) come
    first, so successive weekly runs cycle through the whole catalogue."""
    last_updated = {}
    for u in update_log:
        pid, d = u.get("post_id"), u.get("date", "")
        if pid is not None and (pid not in last_updated or d > last_updated[pid]):
            last_updated[pid] = d
    posts = [e for e in publish_log if e.get("wp_post_id")]
    posts.sort(key=lambda e: last_updated.get(e["wp_post_id"], ""))  # "" (never) sorts first
    return posts[:batch_size]


def fetch_post(site, headers, pid):
    """Fetch the current post (confirms it still exists; returns slug/status/title)."""
    url = f"https://public-api.wordpress.com/rest/v1.1/sites/{site}/posts/{pid}"
    try:
        r = requests.get(url, headers=headers, params={"fields": "ID,status,slug,title"}, timeout=25)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def update_one(entry, site, headers):
    """Regenerate one post with the new confidence pipeline and update it in place."""
    pid       = entry["wp_post_id"]
    keyword   = entry.get("keyword", "")
    post_type = entry.get("post_type", "roundup")

    current = fetch_post(site, headers, pid)
    if not current:
        return "skipped_not_found"

    # Confidence-enriched products for this keyword (cache_builder enriches on the way out).
    products = cb.get_products_for_keyword(keyword, count=8)
    if len(products) < 2:
        return "skipped_no_products"

    content = cg.generate_content(keyword, post_type, products)
    if not content:
        return "failed_generation"

    kd  = {"keyword": keyword, "post_type": post_type, "slug": current.get("slug", "")}
    res = wp.publish_to_wordpress(content, products, kd, update_post_id=pid)
    return "updated" if res else "failed_update"


def run(dry_run=False, batch_size=10):
    publish_log = _load_json(PUBLISH_LOG, [])
    update_log  = _load_json(UPDATE_LOG, [])
    selected    = select_posts(publish_log, update_log, batch_size)

    print(f"post_updater: {len(publish_log)} published posts; selecting {len(selected)} "
          f"(oldest-refresh first); dry_run={dry_run}")

    if dry_run:
        for e in selected:
            print(f"  [dry-run] would update #{e['wp_post_id']} — {e.get('keyword','?')} "
                  f"({e.get('post_type','?')}) — {e.get('title','')[:50]}")
        print(f"  [dry-run] {len(selected)} posts would be processed; no changes made.")
        return

    # Make sure the product caches the generator reads actually exist.
    try:
        cb.build_all_caches()
    except Exception as ex:
        print(f"  [warn] build_all_caches failed: {ex}")

    token   = wp.get_access_token()
    site    = wp.resolve_site(token)
    headers = {"Authorization": f"Bearer {token}"}

    for i, e in enumerate(selected):
        try:
            status = update_one(e, site, headers)
        except Exception as ex:
            status = f"error: {ex}"[:120]
        update_log.append({"date": datetime.date.today().isoformat(),
                           "post_id": e["wp_post_id"], "status": status})
        _save_update_log(update_log)
        print(f"  [{i+1}/{len(selected)}] #{e['wp_post_id']} {e.get('keyword','')[:30]} -> {status}")

        if (i + 1) % SUB_BATCH == 0 and (i + 1) < len(selected):
            print(f"  ... pausing {SUB_BATCH_PAUSE}s (rate limit) ...")
            time.sleep(SUB_BATCH_PAUSE)

    print(f"post_updater: done. {len(selected)} processed.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Re-run published posts through the upgraded generator.")
    ap.add_argument("--dry-run", action="store_true", help="show the plan without making changes")
    ap.add_argument("--batch-size", type=int, default=10, help="max posts to refresh this run")
    args = ap.parse_args()
    run(dry_run=args.dry_run, batch_size=args.batch_size)
