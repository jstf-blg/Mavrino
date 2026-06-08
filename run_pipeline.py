"""
pipeline/run_pipeline.py
─────────────────────────
Main orchestrator. Called by GitHub Actions daily cron.

Flow:
  1. Keyword discovery (if queue is low)
  2. For each keyword in today's batch:
     a. Fetch Amazon product data + reviews
     b. Generate content via Claude API
     c. Render to HTML
  3. Single git commit + push (one Cloudflare build)

All state is stored in config/ JSON files so runs are resumable.
"""

import os, json, time, sys, random
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ── Import pipeline modules ────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from pipeline.pipeline import (
    keyword_discovery as kd,
    amazon_data       as ad,
    content_generator as cg,
    renderer          as rn,
    publisher         as pub,
)

QUEUE_FILE    = Path("config/keyword_queue.json")
DONE_FILE     = Path("config/keywords_done.json")
POSTS_LOG     = Path("config/posts_log.json")
POSTS_PER_DAY = int(os.getenv("POSTS_PER_DAY", "5"))


def load_queue() -> list[dict]:
    if QUEUE_FILE.exists():
        return json.loads(QUEUE_FILE.read_text())
    return []


def save_queue(queue: list[dict]):
    QUEUE_FILE.write_text(json.dumps(queue, indent=2))


def load_done() -> set:
    if DONE_FILE.exists():
        return set(json.loads(DONE_FILE.read_text()))
    return set()


def save_done(done: set):
    DONE_FILE.write_text(json.dumps(list(done), indent=2))


def load_posts_log() -> list[dict]:
    if POSTS_LOG.exists():
        return json.loads(POSTS_LOG.read_text())
    return []


def save_posts_log(log: list[dict]):
    POSTS_LOG.write_text(json.dumps(log[-500:], indent=2))  # keep last 500


def get_asins_for_keyword(keyword_data: dict) -> list[str]:
    """Extract ASINs needed for this keyword."""
    asins = []
    for key in ["asin", "asin_a", "asin_b"]:
        if keyword_data.get(key):
            asins.append(keyword_data[key])
    return asins


def run(dry_run: bool = False):
    today     = datetime.utcnow().strftime("%Y-%m-%d")
    start     = time.time()

    print(f"\n{'='*56}")
    print(f"  Affiliate Pipeline Run — {today}")
    print(f"  Target: {POSTS_PER_DAY} posts")
    print(f"{'='*56}\n")

    # ── Step 1: Refresh keyword queue if running low ────────────────────────
    queue = load_queue()
    if len(queue) < POSTS_PER_DAY * 3:
        print("[pipeline] Queue low — running keyword discovery...")
        kd.run()
        queue = load_queue()

    done = load_done()
    posts_log = load_posts_log()
    published_today = 0
    errors = 0

    # ── Step 2: Process today's batch ──────────────────────────────────────
    batch = queue[:POSTS_PER_DAY]
    remaining = queue[POSTS_PER_DAY:]

    # Render static pages on first run
    static_done = Path("config/.static_done")
    if not static_done.exists():
        rn.render_static_pages()
        static_done.touch()

    for i, keyword_data in enumerate(batch):
        keyword   = keyword_data["keyword"]
        post_type = keyword_data.get("post_type", "roundup")
        slug      = keyword_data.get("slug", keyword[:20])

        print(f"\n[{i+1}/{len(batch)}] '{keyword}' ({post_type})")

        # ── 2a. Fetch product data ────────────────────────────────────────
        asins = get_asins_for_keyword(keyword_data)

        # If no ASINs yet, search Amazon for this keyword
        if not asins:
            products = ad.get_multiple_products([])  # will use Apify search
            # Fallback: use cached products from similar keywords
            cache_files = list(Path("config/product_cache").glob("*.json"))
            if cache_files:
                # Pick random cached products to avoid always using same items
                sample = random.sample(cache_files, min(3, len(cache_files)))
                products = [json.loads(f.read_text()) for f in sample]
            else:
                print(f"  [skip] No product data available for '{keyword}'")
                errors += 1
                continue
        else:
            products = ad.get_multiple_products(asins)

        if not products:
            print(f"  [skip] Could not fetch products for '{keyword}'")
            errors += 1
            continue

        # ── 2b. Generate content ──────────────────────────────────────────
        if not dry_run:
            content = cg.generate_content(keyword, post_type, products)
            if not content:
                print(f"  [skip] Content generation failed for '{keyword}'")
                errors += 1
                continue
        else:
            # Dry run — use placeholder content
            content = {
                "title":            f"Best {keyword} 2026 [DRY RUN]",
                "meta_description": f"Test post for {keyword}",
                "intro":            "Dry run test content.\n\nThis would be real content.",
                "winner_asin":      products[0]["asin"] if products else "",
                "winner_verdict":   "Test verdict.",
                "products":         [],
                "buying_guide":     "Test buying guide.",
                "faq":              [{"q": "Test?", "a": "Yes."}],
                "keyword":          keyword,
                "post_type":        post_type,
                "generated_at":     datetime.utcnow().isoformat(),
            }

        # ── 2c. Render to HTML ─────────────────────────────────────────────
        path = rn.render_and_save(content, products, keyword_data)
        if not path:
            errors += 1
            continue

        # Mark done
        done.add(slug)
        published_today += 1

        # Log it
        posts_log.append({
            "date":      today,
            "keyword":   keyword,
            "post_type": post_type,
            "slug":      slug,
            "title":     content.get("title", ""),
            "products":  len(products),
        })

        # Polite delay between API calls
        if i < len(batch) - 1:
            time.sleep(2)

    # ── Step 3: Update index page ───────────────────────────────────────────
    index_html = rn.render_index(posts_log)
    index_path = Path(os.getenv("OUTPUT_DIR", "output")) / "index.html"
    index_path.write_text(index_html, encoding="utf-8")
    print(f"\n[pipeline] Index updated ({len(posts_log)} posts total)")

    # ── Step 4: Save state ─────────────────────────────────────────────────
    save_queue(remaining)
    save_done(done)
    save_posts_log(posts_log)

    # ── Step 5: Single batch commit + push ────────────────────────────────
    if not dry_run and published_today > 0:
        result = pub.publish_batch()
        print(f"\n[pipeline] Published: {result}")
    elif dry_run:
        print("\n[pipeline] DRY RUN — skipping git push")

    # ── Summary ────────────────────────────────────────────────────────────
    elapsed = time.time() - start
    print(f"\n{'='*56}")
    print(f"  Run complete in {elapsed:.0f}s")
    print(f"  Published today: {published_today}")
    print(f"  Errors: {errors}")
    print(f"  Queue remaining: {len(remaining)}")
    print(f"  Total posts ever: {len(posts_log)}")
    print(f"{'='*56}\n")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    run(dry_run=dry)
