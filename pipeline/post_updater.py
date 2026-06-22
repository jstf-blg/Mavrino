"""pipeline/post_updater.py — re-run published posts through the upgraded generator.

Regenerates each published post with the new confidence-scoring + bias-correction
pipeline and the honest "Mavrino Editorial" byline, then updates the WordPress post in
place (same URL/status). Runs in sub-batches of 10 with 30s pauses to respect WordPress
rate limits, and rotates through the back-catalogue oldest-first so successive runs cover
the whole site.

NO FLATTENING: angled posts (cheapest / splurge / every-budget / worth-it / cheapest-vs-
expensive / most-reviewed) are regenerated WITH their original template angle — read from
config/posts_log.json — so a "cheapest" post never reverts to a generic roundup.

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
import post_templates as ptpl
import taxonomy_manager as tm

ROOT        = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLISH_LOG = os.path.join(ROOT, "logs", "wp_publish_log.json")     # run_pipeline posts (base types)
POSTS_LOG   = os.path.join(ROOT, "config", "posts_log.json")        # scheduler posts (carry "template")
UPDATE_LOG  = os.path.join(ROOT, "logs", "post_update_log.json")

SUB_BATCH       = 10   # process this many, then pause
SUB_BATCH_PAUSE = 30   # seconds between sub-batches (WordPress rate-limit courtesy)

# Legacy template names seen in older log entries → current TEMPLATES keys.
TEMPLATE_NORM = {"budget": "every_budget", "worthit": "worth_it", "cve": "cheapest_vs_expensive"}


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


def _niche(kw):
    """Longest niche-name match in a keyword (singular-aware) — for template rebuilds."""
    kwl = (kw or "").lower()
    best, bl = None, 0
    for children in tm.CATEGORY_HIERARCHY.values():
        for child in children:
            n = child.lower()
            for f in (n, n[:-1] if n.endswith("s") else n):
                if f and f in kwl and len(n) > bl:
                    best, bl = n, len(n)
    return best


def load_all_posts():
    """One entry per wp_post_id, merged from both logs; the scheduler log adds the
    template angle so angled posts can be rebuilt with their angle (no flattening)."""
    merged = {}
    for e in _load_json(PUBLISH_LOG, []):
        pid = e.get("wp_post_id")
        if pid:
            merged[pid] = {"wp_post_id": pid, "keyword": e.get("keyword", ""),
                           "post_type": e.get("post_type", "roundup"),
                           "template": None, "title": e.get("title", "")}
    for e in _load_json(POSTS_LOG, []):
        pid = e.get("wp_post_id")
        if not pid:
            continue
        cur = merged.get(pid, {"wp_post_id": pid})
        cur["keyword"]   = e.get("keyword", cur.get("keyword", ""))
        cur["post_type"] = e.get("post_type", cur.get("post_type", "roundup"))
        if e.get("template"):
            cur["template"] = e.get("template")
        cur.setdefault("template", None)
        cur.setdefault("title", e.get("title", ""))
        merged[pid] = cur
    return list(merged.values())


def select_posts(posts, update_log, batch_size):
    """Oldest-refresh-first rotation so successive weekly runs cycle the whole catalogue."""
    last_updated = {}
    for u in update_log:
        pid, d = u.get("post_id"), u.get("date", "")
        if pid is not None and (pid not in last_updated or d > last_updated[pid]):
            last_updated[pid] = d
    posts = [p for p in posts if p.get("wp_post_id")]
    posts.sort(key=lambda p: last_updated.get(p["wp_post_id"], ""))  # "" (never) sorts first
    return posts[:batch_size]


def fetch_post(site, headers, pid):
    """Confirm the post still exists; return slug/status/title."""
    url = f"https://public-api.wordpress.com/rest/v1.1/sites/{site}/posts/{pid}"
    try:
        r = requests.get(url, headers=headers, params={"fields": "ID,status,slug,title"}, timeout=25)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def update_one(entry, site, headers):
    """Regenerate one post (preserving its template angle) and update it in place."""
    pid       = entry["wp_post_id"]
    keyword   = entry.get("keyword", "")
    post_type = entry.get("post_type", "roundup")
    template  = entry.get("template")

    current = fetch_post(site, headers, pid)
    if not current:
        return "skipped_not_found"

    if template:
        template = TEMPLATE_NORM.get(template, template)
        if template not in ptpl.TEMPLATES:
            template = None   # unknown angle → fall back to the base type rather than guess

    if template:
        # Angled post: rebuild WITH the template so the angle is preserved (no flattening).
        niche = _niche(keyword)
        if not niche:
            return "skipped_no_niche"
        products = cb.get_products_for_keyword(f"best {niche}", count=8)
        if len(products) < 2:
            return "skipped_no_products"
        built = ptpl.build(template, niche, products)
        if not built:
            return "skipped_build_failed"
        kw, used_type, sel = built
        content, publish_products = cg.generate_content(kw, used_type, sel), sel
    else:
        products = cb.get_products_for_keyword(keyword, count=8)
        if len(products) < 2:
            return "skipped_no_products"
        content, publish_products, used_type = cg.generate_content(keyword, post_type, products), products, post_type

    if not content:
        return "failed_generation"

    kd  = {"keyword": content.get("keyword", keyword), "post_type": used_type, "slug": current.get("slug", "")}
    res = wp.publish_to_wordpress(content, publish_products, kd, update_post_id=pid)
    return "updated" if res else "failed_update"


def run(dry_run=False, batch_size=10):
    posts      = load_all_posts()
    update_log = _load_json(UPDATE_LOG, [])
    selected   = select_posts(posts, update_log, batch_size)

    n_tpl = sum(1 for p in selected if p.get("template"))
    print(f"post_updater: {len(posts)} published posts; selecting {len(selected)} "
          f"(oldest-refresh first; {n_tpl} carry a template angle); dry_run={dry_run}")

    if dry_run:
        for e in selected:
            tag = f"[{e['template']}]" if e.get("template") else f"({e.get('post_type','?')})"
            print(f"  [dry-run] would update #{e['wp_post_id']} {tag} — {e.get('keyword','?')} — {e.get('title','')[:46]}")
        print(f"  [dry-run] {len(selected)} posts would be processed; no changes made.")
        return

    try:
        cb.build_all_caches()
    except Exception as ex:
        print(f"  [warn] build_all_caches failed: {ex}")

    token   = wp.get_access_token()
    site    = wp.resolve_site(token)
    headers = {"Authorization": f"Bearer {token}"}

    counts = {}
    for i, e in enumerate(selected):
        try:
            status = update_one(e, site, headers)
        except Exception as ex:
            status = f"error: {ex}"[:120]
        counts[status.split(':')[0]] = counts.get(status.split(':')[0], 0) + 1
        update_log.append({"date": datetime.date.today().isoformat(),
                           "post_id": e["wp_post_id"], "status": status})
        _save_update_log(update_log)
        tag = e.get("template") or e.get("post_type", "")
        print(f"  [{i+1}/{len(selected)}] #{e['wp_post_id']} [{tag}] {e.get('keyword','')[:28]} -> {status}", flush=True)

        if (i + 1) % SUB_BATCH == 0 and (i + 1) < len(selected):
            print(f"  ... pausing {SUB_BATCH_PAUSE}s (rate limit) ...", flush=True)
            time.sleep(SUB_BATCH_PAUSE)

    print(f"post_updater: done. {len(selected)} processed. Tally: {counts}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Re-run published posts through the upgraded generator.")
    ap.add_argument("--dry-run", action="store_true", help="show the plan without making changes")
    ap.add_argument("--batch-size", type=int, default=10, help="max posts to refresh this run")
    args = ap.parse_args()
    run(dry_run=args.dry_run, batch_size=args.batch_size)
