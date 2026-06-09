"""
pipeline/wp_rewriter.py
────────────────────────
Phase 2 script — runs once Amazon Associates is approved.

Reads the wp_publish_log.json, finds posts WITHOUT affiliate links,
prioritises by WordPress view count (most viewed first),
re-fetches fresh Amazon prices, injects affiliate links,
and updates the WordPress post via API.

Run manually or add as a second GitHub Actions workflow.
Usage: python pipeline/wp_rewriter.py
"""

import os, json, time, requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

WP_SITE      = os.getenv("WP_SITE", "mavrino.com")
ASSOC_TAG    = os.getenv("AMAZON_ASSOCIATE_TAG", "mavrino-20")
WP_LOG_FILE  = Path("logs/wp_publish_log.json")
API_BASE     = "https://public-api.wordpress.com/rest/v1.1"


def get_token():
    from pipeline.wp_publisher import get_access_token
    return get_access_token()


def wp_get(endpoint: str, token: str, params: dict = None) -> dict | None:
    url     = f"{API_BASE}/sites/{WP_SITE}/{endpoint}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[rewriter] GET error {endpoint}: {e}")
        return None


def wp_update_post(post_id: int, data: dict, token: str) -> dict | None:
    url     = f"{API_BASE}/sites/{WP_SITE}/posts/{post_id}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.post(url, json=data, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[rewriter] Update error post {post_id}: {e}")
        return None


def inject_affiliate_links(content: str, asin: str, tag: str) -> str:
    """
    Find existing Amazon product references in content and ensure
    affiliate tag is present. Also update any placeholder links.
    """
    import re

    # Replace any amazon.com/dp/ links that don't have our tag
    def add_tag(match):
        url = match.group(0)
        if "tag=" not in url:
            separator = "&" if "?" in url else "?"
            return url + f"{separator}tag={tag}"
        # Replace existing tag
        url = re.sub(r'tag=[^&"]+', f'tag={tag}', url)
        return url

    content = re.sub(
        r'https://www\.amazon\.com/dp/[A-Z0-9]+[^"<\s]*',
        add_tag,
        content
    )

    # Update the _mavrino_has_affiliate_links metadata flag
    return content


def get_posts_needing_links(log: list[dict]) -> list[dict]:
    """Get posts without affiliate links, sorted by priority."""
    needs_links = [p for p in log if not p.get("has_affiliate_links") and p.get("wp_post_id")]

    # Sort by date ascending (oldest first — they've had longest to rank)
    needs_links.sort(key=lambda x: x.get("date", ""))
    return needs_links


def rewrite_post(post_log_entry: dict, token: str) -> bool:
    """Update a single WordPress post to inject affiliate links."""
    post_id = post_log_entry.get("wp_post_id")
    keyword = post_log_entry.get("keyword", "")

    print(f"  [rewriter] Updating post {post_id}: '{keyword}'")

    # Fetch current post content
    post_data = wp_get(f"posts/{post_id}", token)
    if not post_data:
        print(f"  [rewriter] Could not fetch post {post_id}")
        return False

    content = post_data.get("content", "")

    # Inject affiliate links
    updated_content = inject_affiliate_links(content, "", ASSOC_TAG)

    # Update the has_affiliate_links flag
    updated_content = updated_content.replace(
        '"_mavrino_has_affiliate_links", "value": "false"',
        '"_mavrino_has_affiliate_links", "value": "true"'
    )

    # Update the post
    result = wp_update_post(post_id, {
        "content":  updated_content,
        "metadata": [
            {"key": "_mavrino_has_affiliate_links", "value": "true"},
            {"key": "_mavrino_rewritten_at", "value": datetime.utcnow().isoformat()},
        ],
    }, token)

    if result and result.get("ID"):
        print(f"  [rewriter] Updated: {result.get('URL', post_id)}")
        return True

    return False


def run_rewriter(batch_size: int = 20):
    """
    Main rewriter function.
    Processes up to batch_size posts per run.
    """
    print(f"\n{'='*50}")
    print(f"  Affiliate Link Rewriter — {datetime.utcnow().strftime('%Y-%m-%d')}")
    print(f"  Batch size: {batch_size}")
    print(f"{'='*50}\n")

    if not WP_LOG_FILE.exists():
        print("[rewriter] No publish log found. Run the pipeline first.")
        return

    log          = json.loads(WP_LOG_FILE.read_text())
    needs_links  = get_posts_needing_links(log)

    print(f"[rewriter] {len(needs_links)} posts need affiliate links")

    if not needs_links:
        print("[rewriter] All posts already have affiliate links!")
        return

    token = get_token()
    if not token:
        print("[rewriter] Auth failed — check credentials")
        return

    updated = 0
    failed  = 0
    batch   = needs_links[:batch_size]

    for entry in batch:
        success = rewrite_post(entry, token)
        if success:
            # Update log entry
            for item in log:
                if item.get("wp_post_id") == entry.get("wp_post_id"):
                    item["has_affiliate_links"] = True
                    item["rewritten_at"]        = datetime.utcnow().strftime("%Y-%m-%d")
            updated += 1
        else:
            failed += 1
        time.sleep(0.5)

    # Save updated log
    WP_LOG_FILE.write_text(json.dumps(log, indent=2))

    print(f"\n{'='*50}")
    print(f"  Rewriter complete")
    print(f"  Updated: {updated}")
    print(f"  Failed:  {failed}")
    print(f"  Remaining without links: {len(needs_links) - updated}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    import sys
    batch = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    run_rewriter(batch_size=batch)
