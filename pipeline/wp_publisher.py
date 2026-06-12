"""
pipeline/wp_publisher.py
─────────────────────────
Publishes to WordPress.com with:
- Unsplash images per post
- Amazon search links (until Associates approved)
- Mavrino brand author
- Clean top pick callout
- Full taxonomy + SEO via taxonomy_manager
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
ASSOC_TAG        = os.getenv("AMAZON_ASSOCIATE_TAG", "")

TOKEN_FILE  = Path("config/wp_token.json")
WP_LOG_FILE = Path("logs/wp_publish_log.json")
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
        TOKEN_FILE.write_text(json.dumps({
            "access_token": token,
            "cached_at":    time.time(),
        }, indent=2))
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
            r = requests.get(url, params=data or {}, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError:
        print(f"  [wp] HTTP {r.status_code} {endpoint}: {r.text[:200]}")
        return None
    except Exception as e:
        print(f"  [wp] Error {endpoint}: {e}")
        return None


def amazon_search_url(product_title: str) -> str:
    """
    Build an Amazon search URL for a product title.
    TODO: Replace with direct affiliate link once Associates tag approved.
    """
    import urllib.parse
    query = urllib.parse.quote_plus(product_title)
    # If we have an associate tag, add it
    if ASSOC_TAG:
        return f"https://www.amazon.com/s?k={query}&tag={ASSOC_TAG}"
    return f"https://www.amazon.com/s?k={query}"


def build_wp_content(content: dict, products: list[dict], hero_image: dict = None) -> str:
    """Build Gutenberg block HTML from generated content."""
    parts        = []
    products_by_asin = {p.get("asin"): p for p in products}

    # ── Affiliate disclosure ───────────────────────────────────────────────
    parts.append(
        '<!-- wp:paragraph {"className":"affiliate-disclosure","style":{"spacing":{"padding":{"all":"12px"}},"color":{"background":"#fff8e6"}}} -->\n'
        '<p class="affiliate-disclosure" style="background-color:#fff8e6;padding:12px">'
        '<strong>Disclosure:</strong> Mavrino earns commissions from qualifying purchases '
        'made through links on this page. This does not affect our recommendations.</p>\n'
        '<!-- /wp:paragraph -->'
    )

    # ── Hero image from Unsplash ───────────────────────────────────────────
    if hero_image and hero_image.get("url"):
        photographer     = hero_image.get("photographer", "")
        photographer_url = hero_image.get("photographer_url", "")
        unsplash_url     = hero_image.get("unsplash_url", "")
        alt              = hero_image.get("alt", "Product review image")

        parts.append(
            f'<!-- wp:image {{"sizeSlug":"large","align":"wide"}} -->\n'
            f'<figure class="wp-block-image size-large alignwide">'
            f'<img src="{hero_image["url"]}" alt="{alt}" />'
            f'<figcaption class="wp-element-caption">'
            f'Photo by <a href="{photographer_url}?utm_source=mavrino&utm_medium=referral" rel="nofollow">{photographer}</a> '
            f'on <a href="{unsplash_url}?utm_source=mavrino&utm_medium=referral" rel="nofollow">Unsplash</a>'
            f'</figcaption></figure>\n'
            f'<!-- /wp:image -->'
        )

    # ── Intro paragraphs ──────────────────────────────────────────────────
    for para in content.get("intro", "").split('\n\n'):
        if para.strip():
            parts.append(
                f'<!-- wp:paragraph -->\n<p>{para.strip()}</p>\n<!-- /wp:paragraph -->'
            )

    # ── Top pick callout (clean design, not black banner) ─────────────────
    winner_asin    = content.get("winner_asin", "")
    winner_verdict = content.get("winner_verdict", "")
    if winner_verdict:
        winner_product = products_by_asin.get(winner_asin, {})
        winner_title   = winner_product.get("title", "")
        winner_price   = winner_product.get("price", 0)
        winner_rating  = winner_product.get("rating", 0)
        winner_url     = amazon_search_url(winner_title) if winner_title else ""
        stars          = "★" * int(winner_rating)

        parts.append(
            '<!-- wp:group {"className":"top-pick-box","style":{"border":{"width":"2px","color":"#b8431a","radius":"8px"},"spacing":{"padding":{"all":"20px"}},"color":{"background":"#fff8f5"}}} -->\n'
            '<div class="wp-block-group top-pick-box" style="border:2px solid #b8431a;border-radius:8px;padding:20px;background-color:#fff8f5">\n'
            '<!-- wp:paragraph {"style":{"typography":{"fontSize":"12px","fontStyle":"normal","fontWeight":"600","letterSpacing":"2px","textTransform":"uppercase"},"color":{"text":"#b8431a"}}} -->\n'
            '<p style="font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#b8431a">⭐ Our Top Pick</p>\n'
            '<!-- /wp:paragraph -->\n'
            f'<!-- wp:heading {{"level":3,"style":{{"typography":{{"fontSize":"18px"}}}}}} -->\n'
            f'<h3 style="font-size:18px">{winner_title}</h3>\n'
            f'<!-- /wp:heading -->\n'
            f'<!-- wp:paragraph -->\n<p>{winner_verdict}</p>\n<!-- /wp:paragraph -->\n'
        )

        if winner_price:
            parts.append(
                f'<!-- wp:paragraph -->\n'
                f'<p><strong>${winner_price:.2f}</strong>'
                f'{" &nbsp; " + stars if stars else ""}'
                f'{" " + str(winner_rating) + "/5" if winner_rating else ""}</p>\n'
                f'<!-- /wp:paragraph -->\n'
            )

        if winner_url:
            parts.append(
                f'<!-- wp:buttons -->\n<div class="wp-block-buttons">\n'
                f'<!-- wp:button {{"style":{{"color":{{"background":"#b8431a","text":"#ffffff"}},"border":{{"radius":"6px"}}}}}} -->\n'
                f'<div class="wp-block-button">'
                f'<a class="wp-block-button__link" href="{winner_url}" rel="nofollow sponsored" target="_blank">'
                f'Search on Amazon →</a></div>\n'
                f'<!-- /wp:button -->\n</div>\n<!-- /wp:buttons -->\n'
            )

        parts.append('</div>\n<!-- /wp:group -->')

    # ── Product cards ──────────────────────────────────────────────────────
    for item in content.get("products", []):
        asin    = item.get("asin", "")
        product = products_by_asin.get(asin, {})
        title   = product.get("title", asin)
        price   = product.get("price", 0)
        rating  = product.get("rating", 0)
        reviews = product.get("review_count", 0)
        img_url = product.get("image_url", "")
        aff_url = amazon_search_url(title)
        stars   = "★" * int(rating) + ("½" if rating % 1 >= 0.5 else "")

        # Try to get Unsplash image if no product image
        if not img_url:
            try:
                import image_fetcher as imf
                img_url = imf.get_product_image(title)
            except Exception:
                pass

        card = (
            f'<!-- wp:group {{"className":"product-card","style":{{"border":{{"width":"1px","color":"#e4e0d8","radius":"8px"}},"spacing":{{"padding":{{"all":"20px"}}}}}}}} -->\n'
            f'<div class="wp-block-group product-card" style="border:1px solid #e4e0d8;border-radius:8px;padding:20px">\n'
        )

        # Product image
        if img_url:
            card += (
                f'<!-- wp:image {{"sizeSlug":"medium","align":"right"}} -->\n'
                f'<figure class="wp-block-image size-medium alignright">'
                f'<a href="{aff_url}" rel="nofollow sponsored" target="_blank">'
                f'<img src="{img_url}" alt="{title}" /></a></figure>\n'
                f'<!-- /wp:image -->\n'
            )

        # Heading label
        if item.get("heading"):
            card += (
                f'<!-- wp:paragraph {{"style":{{"typography":{{"fontSize":"11px","fontWeight":"700","letterSpacing":"2px","textTransform":"uppercase"}},"color":{{"text":"#8a8480"}}}}}} -->\n'
                f'<p style="font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#8a8480">{item["heading"]}</p>\n'
                f'<!-- /wp:paragraph -->\n'
            )

        # Product title
        card += (
            f'<!-- wp:heading {{"level":3}} -->\n'
            f'<h3>{title}</h3>\n'
            f'<!-- /wp:heading -->\n'
        )

        # Price and rating
        if price or rating:
            price_str  = f"<strong>${price:.2f}</strong>" if price else ""
            rating_str = f"{stars} {rating}/5 ({reviews:,} reviews)" if rating else ""
            card += (
                f'<!-- wp:paragraph -->\n'
                f'<p>{price_str}{"&nbsp;&nbsp;" if price_str and rating_str else ""}{rating_str}</p>\n'
                f'<!-- /wp:paragraph -->\n'
            )

        # Verdict
        if item.get("verdict"):
            card += f'<!-- wp:paragraph -->\n<p>{item["verdict"]}</p>\n<!-- /wp:paragraph -->\n'

        # Best for
        if item.get("who_its_for"):
            card += (
                f'<!-- wp:paragraph -->\n'
                f'<p>👤 <strong>Best for:</strong> {item["who_its_for"]}</p>\n'
                f'<!-- /wp:paragraph -->\n'
            )

        # Pros and cons
        if item.get("main_pro") or item.get("main_con"):
            card += (
                f'<!-- wp:columns -->\n<div class="wp-block-columns">\n'
                f'<!-- wp:column -->\n<div class="wp-block-column">\n'
                f'<!-- wp:paragraph -->\n<p>✅ <strong>Pro:</strong> {item.get("main_pro","")}</p>\n<!-- /wp:paragraph -->\n'
                f'</div>\n<!-- /wp:column -->\n'
                f'<!-- wp:column -->\n<div class="wp-block-column">\n'
                f'<!-- wp:paragraph -->\n<p>⚠️ <strong>Consider:</strong> {item.get("main_con","")}</p>\n<!-- /wp:paragraph -->\n'
                f'</div>\n<!-- /wp:column -->\n'
                f'</div>\n<!-- /wp:columns -->\n'
            )

        # Review quote
        if item.get("quote"):
            card += (
                f'<!-- wp:quote -->\n'
                f'<blockquote class="wp-block-quote">'
                f'<p>{item["quote"]}</p>'
                f'<cite>Verified Amazon buyer</cite>'
                f'</blockquote>\n'
                f'<!-- /wp:quote -->\n'
            )

        # CTA button
        card += (
            f'<!-- wp:buttons -->\n<div class="wp-block-buttons">\n'
            f'<!-- wp:button {{"style":{{"color":{{"background":"#1c1814","text":"#ffffff"}},"border":{{"radius":"6px"}}}}}} -->\n'
            f'<div class="wp-block-button">'
            f'<a class="wp-block-button__link" href="{aff_url}" rel="nofollow sponsored" target="_blank">'
            f'Search on Amazon →</a></div>\n'
            f'<!-- /wp:button -->\n</div>\n<!-- /wp:buttons -->\n'
        )

        card += '</div>\n<!-- /wp:group -->\n'
        parts.append(card)

    # ── Buying guide ───────────────────────────────────────────────────────
    if content.get("buying_guide"):
        parts.append('<!-- wp:heading {"level":2} -->\n<h2>How to Choose</h2>\n<!-- /wp:heading -->')
        for para in content["buying_guide"].split('\n\n'):
            if para.strip():
                parts.append(f'<!-- wp:paragraph -->\n<p>{para.strip()}</p>\n<!-- /wp:paragraph -->')

    # ── FAQ ────────────────────────────────────────────────────────────────
    faq = content.get("faq", [])
    if faq:
        parts.append('<!-- wp:heading {"level":2} -->\n<h2>Frequently Asked Questions</h2>\n<!-- /wp:heading -->')
        for item in faq:
            if item.get("q"):
                parts.append(f'<!-- wp:heading {{"level":3}} -->\n<h3>{item["q"]}</h3>\n<!-- /wp:heading -->')
            if item.get("a"):
                parts.append(f'<!-- wp:paragraph -->\n<p>{item["a"]}</p>\n<!-- /wp:paragraph -->')

    # ── Author box — Mavrino brand ─────────────────────────────────────────
    author_bio = os.getenv("AUTHOR_BIO", "Mavrino tests home, kitchen, travel and lifestyle products to help US shoppers buy with confidence.")
    parts.append(
        '<!-- wp:group {"className":"author-box","style":{"border":{"width":"1px","color":"#e4e0d8","radius":"8px"},"spacing":{"padding":{"all":"16px"}},"color":{"background":"#f5f2ec"}}} -->\n'
        '<div class="wp-block-group author-box" style="border:1px solid #e4e0d8;border-radius:8px;padding:16px;background-color:#f5f2ec">\n'
        '<!-- wp:paragraph -->\n'
        f'<p><strong>Mavrino Editorial</strong> — {author_bio}</p>\n'
        '<!-- /wp:paragraph -->\n'
        '</div>\n<!-- /wp:group -->'
    )

    return "\n\n".join(parts)


def publish_to_wordpress(content: dict, products: list[dict], keyword_data: dict) -> dict | None:
    """Publish a post to WordPress with taxonomy, SEO, images."""
    token = get_access_token()
    if not token:
        return None

    keyword = content.get("keyword", "")
    title   = content.get("title", keyword)

    # Fetch hero image from Unsplash
    hero_image = None
    try:
        import image_fetcher as imf
        hero_image = imf.get_hero_image(keyword)
        if hero_image:
            print(f"  [images] Got hero image: {hero_image.get('alt','')[:40]}")
    except Exception as e:
        print(f"  [images] Could not fetch image: {e}")

    # Build content
    wp_content = build_wp_content(content, products, hero_image)

    # Process through taxonomy manager
    try:
        import taxonomy_manager as tm
        processed      = tm.process_post(content, products, keyword, wp_content, token)
        wp_content     = processed["content"]
        category_ids   = processed["categories"]
        tag_ids        = processed["tags"]
        seo_metadata   = processed["metadata"]
        classification = processed["classification"]
    except Exception as e:
        print(f"  [wp] Taxonomy error: {e} — using fallback")
        category_ids   = []
        tag_ids        = []
        seo_metadata   = [{"key": "_mavrino_has_affiliate_links", "value": "false"}]
        classification = {}

    # Excerpt from intro
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

    print(f"  [wp] Publishing '{title[:55]}...'")
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
        "total":          len(log),
        "with_links":     len([p for p in log if p.get("has_affiliate_links")]),
        "without_links":  len([p for p in log if not p.get("has_affiliate_links")]),
        "last_published": log[-1].get("date") if log else None,
    }


if __name__ == "__main__":
    token = get_access_token()
    if token:
        print(f"Auth OK — token: {token[:20]}...")
        print(json.dumps(get_wp_stats(), indent=2))
    else:
        print("Auth failed")
