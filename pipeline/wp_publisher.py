"""
pipeline/wp_publisher.py
─────────────────────────
Publishes to WordPress.com with full taxonomy + SEO via taxonomy_manager.
"""

import os, json, time, requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

WP_CLIENT_ID     = os.getenv("WP_CLIENT_ID")
WP_CLIENT_SECRET = os.getenv("WP_CLIENT_SECRET")
WP_SITE          = os.getenv("WP_SITE", "mavrino.com")
WP_USERNAME      = os.getenv("WP_USERNAME")
WP_PASSWORD      = os.getenv("WP_PASSWORD")

TOKEN_FILE   = Path("config/wp_token.json")
WP_LOG_FILE  = Path("logs/wp_publish_log.json")
WP_LOG_FILE.parent.mkdir(exist_ok=True)

API_BASE        = "https://public-api.wordpress.com/rest/v1.1"
OAUTH_TOKEN_URL = "https://public-api.wordpress.com/oauth2/token"


def get_access_token() -> str | None:
    if TOKEN_FILE.exists():
        try:
            cached = json.loads(TOKEN_FILE.read_text())
            if time.time() - cached.get("cached_at", 0) < 30 * 86400 and cached.get("access_token"):
                return cached["access_token"]
        except Exception:
            pass

    print("[wp] Authenticating with WordPress.com...")
    try:
        r = requests.post(OAUTH_TOKEN_URL, data={
            "client_id":     WP_CLIENT_ID,
            "client_secret": WP_CLIENT_SECRET,
            "grant_type":    "password",
            "username":      WP_USERNAME,
            "password":      WP_PASSWORD,
        }, timeout=15)
        r.raise_for_status()
        data  = r.json()
        token = data.get("access_token")
        if not token:
            print(f"[wp] Auth failed: {data}")
            return None
        TOKEN_FILE.write_text(json.dumps({"access_token": token, "cached_at": time.time()}, indent=2))
        print("[wp] Authenticated successfully")
        return token
    except Exception as e:
        print(f"[wp] Auth error: {e}")
        return None


def wp_request(method: str, endpoint: str, token: str, data: dict = None) -> dict | None:
    url     = f"{API_BASE}/sites/{WP_SITE}/{endpoint}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        if method == "POST":
            r = requests.post(url, json=data, headers=headers, timeout=30)
        else:
            r = requests.get(url, params=data, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        print(f"  [wp] HTTP {r.status_code} {endpoint}: {r.text[:200]}")
        return None
    except Exception as e:
        print(f"  [wp] Error {endpoint}: {e}")
        return None


def build_wp_content(content: dict, products: list[dict]) -> str:
    """Build Gutenberg block HTML from generated content."""
    parts = []

    # Disclosure
    parts.append('''<!-- wp:paragraph {"className":"affiliate-disclosure","style":{"color":{"background":"#fff8e6"}}} -->
<p class="affiliate-disclosure" style="background-color:#fff8e6"><strong>Disclosure:</strong> Mavrino earns commissions from qualifying purchases made through links on this page. This does not affect our recommendations.</p>
<!-- /wp:paragraph -->''')

    # Intro
    for para in content.get("intro", "").split('\n\n'):
        if para.strip():
            parts.append(f'<!-- wp:paragraph -->\n<p>{para.strip()}</p>\n<!-- /wp:paragraph -->')

    # Winner banner
    winner_asin    = content.get("winner_asin", "")
    winner_verdict = content.get("winner_verdict", "")
    products_by_asin = {p.get("asin"): p for p in products}
    if winner_verdict:
        winner_title = products_by_asin.get(winner_asin, {}).get("title", "")
        parts.append(f'''<!-- wp:group {{"style":{{"color":{{"background":"#1c1814","text":"#ffffff"}},"spacing":{{"padding":{{"all":"20px"}}}}}}}} -->
<div class="wp-block-group" style="background-color:#1c1814;color:#ffffff;padding:20px">
<!-- wp:paragraph -->
<p>🏆 <strong>Our Top Pick{": " + winner_title if winner_title else ""}</strong><br>{winner_verdict}</p>
<!-- /wp:paragraph -->
</div>
<!-- /wp:group -->''')

    # Product cards
    for item in content.get("products", []):
        asin    = item.get("asin", "")
        product = products_by_asin.get(asin, {})
        title   = product.get("title", asin)
        price   = product.get("price", 0)
        rating  = product.get("rating", 0)
        reviews = product.get("review_count", 0)
        aff_url = product.get("affiliate_url", f"https://www.amazon.com/dp/{asin}?tag=mavrino-20")
        img_url = product.get("image_url", "")

        stars = "★" * int(rating) + ("½" if rating % 1 >= 0.5 else "")

        card = f'<!-- wp:group {{"className":"product-card","style":{{"border":{{"width":"1px","color":"#e4e0d8"}},"spacing":{{"padding":{{"all":"20px"}}}}}}}} -->\n<div class="wp-block-group product-card" style="border:1px solid #e4e0d8;padding:20px">\n'

        # Product image
        if img_url:
            card += f'<!-- wp:image {{"sizeSlug":"medium","linkDestination":"custom"}} -->\n<figure class="wp-block-image size-medium"><a href="{aff_url}" rel="nofollow sponsored" target="_blank"><img src="{img_url}" alt="{title}" /></a></figure>\n<!-- /wp:image -->\n'

        card += f'<!-- wp:paragraph -->\n<p><strong>{item.get("heading","")}</strong></p>\n<!-- /wp:paragraph -->\n'
        card += f'<!-- wp:heading {{"level":3}} -->\n<h3>{title}</h3>\n<!-- /wp:heading -->\n'

        if price:
            card += f'<!-- wp:paragraph -->\n<p><strong>${price:.2f}</strong> &nbsp; {stars} {rating}/5 ({reviews:,} reviews)</p>\n<!-- /wp:paragraph -->\n'

        if item.get("verdict"):
            card += f'<!-- wp:paragraph -->\n<p>{item["verdict"]}</p>\n<!-- /wp:paragraph -->\n'

        if item.get("who_its_for"):
            card += f'<!-- wp:paragraph -->\n<p>👤 <strong>Best for:</strong> {item["who_its_for"]}</p>\n<!-- /wp:paragraph -->\n'

        if item.get("main_pro") or item.get("main_con"):
            card += f'<!-- wp:columns -->\n<div class="wp-block-columns">\n<!-- wp:column -->\n<div class="wp-block-column">\n<!-- wp:paragraph -->\n<p>✅ <strong>Pro:</strong> {item.get("main_pro","")}</p>\n<!-- /wp:paragraph -->\n</div>\n<!-- /wp:column -->\n<!-- wp:column -->\n<div class="wp-block-column">\n<!-- wp:paragraph -->\n<p>⚠️ <strong>Consider:</strong> {item.get("main_con","")}</p>\n<!-- /wp:paragraph -->\n</div>\n<!-- /wp:column -->\n</div>\n<!-- /wp:columns -->\n'

        if item.get("quote"):
            card += f'<!-- wp:quote -->\n<blockquote class="wp-block-quote"><p>{item["quote"]}</p><cite>Verified Amazon buyer</cite></blockquote>\n<!-- /wp:quote -->\n'

        if aff_url:
            card += f'<!-- wp:buttons -->\n<div class="wp-block-buttons"><!-- wp:button {{"style":{{"color":{{"background":"#1c1814","text":"#ffffff"}}}}}} -->\n<div class="wp-block-button"><a class="wp-block-button__link" href="{aff_url}" rel="nofollow sponsored" target="_blank">Check price on Amazon →</a></div>\n<!-- /wp:button --></div>\n<!-- /wp:buttons -->\n'

        card += '</div>\n<!-- /wp:group -->'
        parts.append(card)

    # Buying guide
    if content.get("buying_guide"):
        parts.append('<!-- wp:heading {"level":2} -->\n<h2>How to Choose</h2>\n<!-- /wp:heading -->')
        for para in content["buying_guide"].split('\n\n'):
            if para.strip():
                parts.append(f'<!-- wp:paragraph -->\n<p>{para.strip()}</p>\n<!-- /wp:paragraph -->')

    # FAQ
    for item in content.get("faq", []):
        parts.append(f'<!-- wp:heading {{"level":3}} -->\n<h3>{item.get("q","")}</h3>\n<!-- /wp:heading -->')
        parts.append(f'<!-- wp:paragraph -->\n<p>{item.get("a","")}</p>\n<!-- /wp:paragraph -->')

    # Author
    parts.append(f'''<!-- wp:group {{"style":{{"color":{{"background":"#f5f2ec"}},"spacing":{{"padding":{{"all":"16px"}}}}}}}} -->
<div class="wp-block-group" style="background-color:#f5f2ec;padding:16px">
<!-- wp:paragraph -->
<p><strong>{os.getenv("AUTHOR_NAME","Mavrino")}</strong> — {os.getenv("AUTHOR_BIO","Consumer product researcher.")}</p>
<!-- /wp:paragraph -->
</div>
<!-- /wp:group -->''')

    return "\n\n".join(parts)


def publish_to_wordpress(content: dict, products: list[dict], keyword_data: dict) -> dict | None:
    """Publish a post to WordPress with full taxonomy + SEO."""
    token = get_access_token()
    if not token:
        return None

    keyword   = content.get("keyword", "")
    title     = content.get("title", keyword)

    # Build raw content first
    wp_content = build_wp_content(content, products)

    # Process through taxonomy manager (adds schema, categories, tags, internal links)
    try:
        import taxonomy_manager as tm
        processed = tm.process_post(content, products, keyword, wp_content, token)
        wp_content   = processed["content"]
        category_ids = processed["categories"]
        tag_ids      = processed["tags"]
        seo_metadata = processed["metadata"]
        classification = processed["classification"]
    except Exception as e:
        print(f"  [wp] Taxonomy manager error: {e} — using fallback")
        category_ids   = []
        tag_ids        = []
        seo_metadata   = [{"key": "_mavrino_has_affiliate_links", "value": "false"}]
        classification = {}

    # Build post excerpt
    intro   = content.get("intro", "")
    excerpt = intro.split('\n\n')[0][:200] if intro else ""

    post_data = {
        "title":      title,
        "content":    wp_content,
        "excerpt":    excerpt,
        "status":     "publish",
        "categories": category_ids,
        "tags":       tag_ids,
        "format":     "standard",
        "metadata":   seo_metadata,
    }

    print(f"  [wp] Publishing '{title[:50]}...'")
    result = wp_request("POST", "posts/new", token, post_data)

    if result and result.get("ID"):
        post_id  = result["ID"]
        post_url = result.get("URL", "")
        print(f"  [wp] Published: {post_url} (ID: {post_id})")

        log_entry = {
            "date":       datetime.utcnow().strftime("%Y-%m-%d"),
            "keyword":    keyword,
            "post_type":  content.get("post_type", "roundup"),
            "wp_post_id": post_id,
            "wp_url":     post_url,
            "title":      title,
            "category":   f"{classification.get('parent_category','')}/{classification.get('child_category','')}",
            "has_affiliate_links": False,
        }

        # Record in taxonomy index for future internal linking
        try:
            import taxonomy_manager as tm
            tm.record_published_post(log_entry, classification)
        except Exception:
            pass

        _append_wp_log(log_entry)
        return log_entry

    return None


def _append_wp_log(entry: dict):
    log = []
    if WP_LOG_FILE.exists():
        try:
            log = json.loads(WP_LOG_FILE.read_text())
        except Exception:
            log = []
    log.append(entry)
    WP_LOG_FILE.write_text(json.dumps(log[-1000:], indent=2))


def get_wp_stats() -> dict:
    if not WP_LOG_FILE.exists():
        return {"total": 0, "with_links": 0, "without_links": 0}
    log = json.loads(WP_LOG_FILE.read_text())
    return {
        "total":         len(log),
        "with_links":    len([p for p in log if p.get("has_affiliate_links")]),
        "without_links": len([p for p in log if not p.get("has_affiliate_links")]),
        "last_published": log[-1].get("date") if log else None,
    }


if __name__ == "__main__":
    token = get_access_token()
    if token:
        print(f"Auth OK — token: {token[:20]}...")
        print(json.dumps(get_wp_stats(), indent=2))
    else:
        print("Auth failed")
