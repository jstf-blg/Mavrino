"""
run_pipeline.py — Mavrino automated content pipeline
Uses cache_builder for smart product matching per keyword.
"""

import os, json, time, sys, random
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

_pipeline_dir = str(Path(__file__).parent / 'pipeline')
sys.path.insert(0, _pipeline_dir)

import keyword_discovery as kd
import amazon_data       as ad
import content_generator as cg
import wp_publisher      as wp
import cache_builder     as cb

QUEUE_FILE    = Path("config/keyword_queue.json")
DONE_FILE     = Path("config/keywords_done.json")
POSTS_LOG     = Path("config/posts_log.json")
POSTS_PER_DAY = int(os.getenv("POSTS_PER_DAY", "5"))


def load_queue() -> list:
    if QUEUE_FILE.exists():
        return json.loads(QUEUE_FILE.read_text())
    return []

def save_queue(queue: list):
    QUEUE_FILE.write_text(json.dumps(queue, indent=2))

def load_done() -> set:
    if DONE_FILE.exists():
        return set(json.loads(DONE_FILE.read_text()))
    return set()

def save_done(done: set):
    DONE_FILE.write_text(json.dumps(list(done), indent=2))

def load_posts_log() -> list:
    if POSTS_LOG.exists():
        return json.loads(POSTS_LOG.read_text())
    return []

def save_posts_log(log: list):
    POSTS_LOG.write_text(json.dumps(log[-1000:], indent=2))

def get_asins_for_keyword(keyword_data: dict) -> list:
    asins = []
    for key in ["asin", "asin_a", "asin_b"]:
        if keyword_data.get(key):
            asins.append(keyword_data[key])
    return asins


def run(dry_run: bool = False):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    start = time.time()

    print(f"\n{'='*56}")
    print(f"  Mavrino Pipeline — {today}")
    print(f"  Target: {POSTS_PER_DAY} posts → WordPress")
    print(f"{'='*56}\n")

    # ── Step 1: Build product cache ───────────────────────────────────────
    print("[pipeline] Refreshing product cache...")
    cb.build_all_caches()

    # ── Step 2: Refresh keyword queue if low ──────────────────────────────
    queue = load_queue()
    if len(queue) < POSTS_PER_DAY * 3:
        print("[pipeline] Queue low — running keyword discovery...")
        kd.run()
        queue = load_queue()

    done      = load_done()
    posts_log = load_posts_log()
    published = 0
    errors    = 0
    batch     = queue[:POSTS_PER_DAY]
    remaining = queue[POSTS_PER_DAY:]

    for i, keyword_data in enumerate(batch):
        keyword   = keyword_data["keyword"]
        post_type = keyword_data.get("post_type", "roundup")
        slug      = keyword_data.get("slug", keyword[:20])

        print(f"\n[{i+1}/{len(batch)}] '{keyword}' ({post_type})")

        # ── Fetch product data ─────────────────────────────────────────────
        asins = get_asins_for_keyword(keyword_data)

        if asins:
            products = ad.get_multiple_products(asins)
        else:
            # Use cache builder for smart keyword matching
            products = cb.get_products_for_keyword(keyword, count=3)

        if not products:
            print(f"  [skip] No product data for '{keyword}'")
            errors += 1
            continue

        # ── Generate content ───────────────────────────────────────────────
        if dry_run:
            content = {
                "title":            f"Best {keyword} 2026 [DRY RUN]",
                "meta_description": f"Test post for {keyword}",
                "intro":            "Dry run test.\n\nThis would be real content.",
                "winner_asin":      products[0]["asin"] if products else "",
                "winner_verdict":   "Test verdict.",
                "products":         [],
                "buying_guide":     "Test buying guide.",
                "faq":              [{"q": "Test?", "a": "Yes."}],
                "keyword":          keyword,
                "post_type":        post_type,
                "generated_at":     datetime.utcnow().isoformat(),
            }
        else:
            content = cg.generate_content(keyword, post_type, products)
            if not content:
                print(f"  [skip] Content generation failed for '{keyword}'")
                errors += 1
                continue

        # ── Publish to WordPress ───────────────────────────────────────────
        if dry_run:
            print(f"  [dry-run] Would publish: {content['title']}")
            print(f"  [dry-run] Products: {[p.get('title','?')[:40] for p in products]}")
            result = {"wp_post_id": 0, "wp_url": "dry-run", "title": content["title"]}
        else:
            result = wp.publish_to_wordpress(content, products, keyword_data)

        if not result:
            errors += 1
            continue

        # ── Log it ─────────────────────────────────────────────────────────
        done.add(slug)
        published += 1
        posts_log.append({
            "date":       today,
            "keyword":    keyword,
            "post_type":  post_type,
            "slug":       slug,
            "title":      content.get("title", ""),
            "wp_post_id": result.get("wp_post_id", 0),
            "wp_url":     result.get("wp_url", ""),
            "products":   len(products),
        })

        if i < len(batch) - 1:
            time.sleep(30)

    # ── Save state ─────────────────────────────────────────────────────────
    save_queue(remaining)
    save_done(done)
    save_posts_log(posts_log)

    # ── Summary ────────────────────────────────────────────────────────────
    elapsed = time.time() - start
    print(f"\n{'='*56}")
    print(f"  Run complete in {elapsed:.0f}s")
    print(f"  Published: {published} | Errors: {errors}")
    print(f"  Queue remaining: {len(remaining)}")
    print(f"  Total posts ever: {len(posts_log)}")
    print(f"{'='*56}\n")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    run(dry_run=dry)
