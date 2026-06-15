"""
newsletter.py — auto-generate a weekly "best of" digest and publish it.
─────────────────────────────────────────────────────────────────────────
Reads the past 7 days of published guides (+ their Mavrino Scores and any
price drops), writes a short branded digest post, and publishes it. With
Jetpack Subscriptions enabled, publishing the digest emails it to subscribers
(those on the weekly cadence receive it as their roundup). Schedule weekly via
.github/workflows/newsletter.yml. Safe no-op if nothing was published this week.
"""

import os, sys, json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent / "pipeline"))

import wp_publisher as wp

LOG = Path("logs/wp_publish_log.json")
SITE = os.getenv("SITE_DOMAIN", "https://mavrino.com").rstrip("/")


def recent_posts(days: int = 7) -> list:
    if not LOG.exists():
        return []
    try:
        log = json.loads(LOG.read_text())
    except Exception:
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    seen, out = set(), []
    for e in reversed(log):                     # newest first
        url = e.get("wp_url", "")
        if not url or url in seen:
            continue
        if (e.get("date", "") or "") < cutoff:
            continue
        seen.add(url)
        out.append(e)
    return out


def build_digest(posts: list) -> dict | None:
    if not posts:
        return None
    # group by vertical (category = "Parent/Child")
    by_vertical = {}
    for p in posts:
        vert = (p.get("category", "") or "/").split("/")[0] or "Latest"
        by_vertical.setdefault(vert, []).append(p)

    blocks = [
        '<!-- wp:paragraph -->\n<p>Here are the buying guides we published this week — '
        'each one ranked with real ratings, thousands of verified reviews, and our '
        'data-driven Mavrino Score. Happy shopping.</p>\n<!-- /wp:paragraph -->'
    ]
    for vert in sorted(by_vertical):
        items = by_vertical[vert]
        blocks.append(f'<!-- wp:heading {{"level":2}} -->\n<h2>{vert}</h2>\n<!-- /wp:heading -->')
        lis = "".join(
            f'<!-- wp:list-item --><li><a href="{p.get("wp_url")}">{p.get("title","")}</a></li><!-- /wp:list-item -->'
            for p in items
        )
        blocks.append(f'<!-- wp:list -->\n<ul class="wp-block-list">{lis}</ul>\n<!-- /wp:list -->')

    blocks.append(
        '<!-- wp:paragraph -->\n<p>Want these in your inbox every week? '
        'Subscribe below and never miss our latest picks.</p>\n<!-- /wp:paragraph -->'
    )
    blocks.append(_subscribe_block())

    today = datetime.now(timezone.utc)
    title = f"This Week at Mavrino: {len(posts)} New Buying Guides ({today:%b %d})"
    return {"title": title, "content": "\n\n".join(blocks)}


def _subscribe_block() -> str:
    return ('<!-- wp:shortcode -->\n'
            '[jetpack_subscription_form title="Get our weekly picks" '
            'subscribe_text="New, data-ranked buying guides straight to your inbox. No spam." '
            'subscribe_button="Subscribe"]\n'
            '<!-- /wp:shortcode -->')


def run():
    posts = recent_posts(7)
    digest = build_digest(posts)
    if not digest:
        print("[newsletter] nothing published this week — skipping")
        return
    token = wp.get_access_token()
    site  = wp.resolve_site(token)
    data = {"title": digest["title"], "content": digest["content"], "status": "publish",
            "categories": ["News"], "format": "standard"}
    r = wp.wp_request("POST", "posts/new", token, data)
    if r and r.get("ID"):
        wp.wp_request("POST", f"posts/{r['ID']}", token, {"discussion": {"pings_open": False}})
        print(f"[newsletter] published digest: {r.get('URL')} ({len(posts)} guides)")
    else:
        print("[newsletter] publish failed")


if __name__ == "__main__":
    run()
