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

import os, json, time, re, requests
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

_RESOLVED_SITE: str | None = None


def resolve_site(token: str = None) -> str:
    """Return a numeric blog ID for WP_SITE.

    The WP.com API rejects calls addressed by a mapped primary domain on some
    sites (HTTP 403 'API calls to this blog have been disabled'), but the numeric
    blog ID always works. So if WP_SITE is a domain, resolve it to its ID via
    /me/sites once and cache it — this keeps the pipeline working no matter
    whether the WP_SITE secret/env is set to the domain or the numeric ID.
    """
    global _RESOLVED_SITE
    if _RESOLVED_SITE:
        return _RESOLVED_SITE
    if str(WP_SITE).isdigit():
        _RESOLVED_SITE = str(WP_SITE)
        return _RESOLVED_SITE
    try:
        tok    = token or get_access_token()
        r      = requests.get(f"{API_BASE}/me/sites",
                              headers={"Authorization": f"Bearer {tok}"}, timeout=15)
        needle = str(WP_SITE).strip("/").lower().replace("https://", "").replace("http://", "")
        for s in r.json().get("sites", []):
            if needle and needle in s.get("URL", "").lower():
                _RESOLVED_SITE = str(s.get("ID"))
                return _RESOLVED_SITE
    except Exception as e:
        print(f"  [wp] Could not resolve site ID for '{WP_SITE}': {e}")
    _RESOLVED_SITE = str(WP_SITE)
    return _RESOLVED_SITE


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
    url     = f"{API_BASE}/sites/{resolve_site(token)}/{endpoint}"
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


_MEDIA_CACHE: dict = {}

def _slug(text: str, maxlen: int = 40) -> str:
    """Filesystem/URL-safe slug for media filenames."""
    import re
    s = re.sub(r"[^a-z0-9]+", "-", (text or "image").lower()).strip("-")
    return (s[:maxlen] or "image")


def upload_media_from_url(image_url: str, token: str, filename: str = "image") -> dict | None:
    """Download an external image and sideload it into the WordPress media library.

    Returns {"ID": int, "URL": str} for the WP-hosted copy, or None on failure.

    WordPress.com strips hot-linked external <img> tags (and invalidates the blocks
    that contain them) when rendering a post, so every image that needs to appear
    on the live site must first be uploaded here and referenced by its WP URL/ID.
    Results are cached per-process so each distinct source URL uploads at most once.
    """
    if not image_url:
        return None
    if image_url in _MEDIA_CACHE:
        return _MEDIA_CACHE[image_url]

    result = None
    try:
        resp = requests.get(image_url, timeout=30)
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        if "image" not in ctype:
            _MEDIA_CACHE[image_url] = None
            return None
        ext = ".png" if "png" in ctype else ".webp" if "webp" in ctype else ".jpg"
        fname = _slug(filename) + ext
        r = requests.post(
            f"{API_BASE}/sites/{resolve_site(token)}/media/new",
            headers={"Authorization": f"Bearer {token}"},
            files={"media[]": (fname, resp.content, ctype)},
            timeout=90,
        )
        r.raise_for_status()
        media = r.json().get("media", [])
        if media:
            result = {"ID": media[0].get("ID"), "URL": media[0].get("URL")}
    except Exception as e:
        print(f"  [wp] Media upload failed ({filename}): {e}")

    _MEDIA_CACHE[image_url] = result
    return result


_IMG_OK_CACHE: dict = {}

def _image_ok(url: str) -> bool:
    """Return True if an image URL is actually reachable (HTTP 200).

    Seed Amazon image URLs are frequently stale (404) or hotlink-blocked, which
    produces broken images in posts. Results are cached per-process so we issue
    at most one HEAD request per distinct URL per run.
    """
    if not url:
        return False
    if url in _IMG_OK_CACHE:
        return _IMG_OK_CACHE[url]
    ok = False
    try:
        r = requests.head(url, timeout=8, allow_redirects=True)
        ok = (r.status_code == 200)
    except Exception:
        ok = False
    _IMG_OK_CACHE[url] = ok
    return ok


def build_wp_content(content: dict, products: list[dict], hero_image: dict = None,
                     hero_media: dict = None, image_map: dict = None) -> str:
    """Build Gutenberg block HTML from generated content.

    hero_media / image_map carry WordPress-hosted media ({"ID","URL"}) produced by
    upload_media_from_url. We reference those (never the raw external URLs) and tag
    each <img> with its wp-image-<id> class so the core/image blocks validate and
    survive WordPress.com's render-time sanitisation.
    """
    parts        = []
    products_by_asin = {p.get("asin"): p for p in products}
    image_map    = image_map or {}

    # ── Affiliate disclosure ───────────────────────────────────────────────
    parts.append(
        '<!-- wp:paragraph {"className":"affiliate-disclosure","style":{"spacing":{"padding":{"all":"12px"}},"color":{"background":"#fff8e6"}}} -->\n'
        '<p class="affiliate-disclosure" style="background-color:#fff8e6;padding:12px">'
        '<strong>Disclosure:</strong> Mavrino earns commissions from qualifying purchases '
        'made through links on this page. This does not affect our recommendations.</p>\n'
        '<!-- /wp:paragraph -->'
    )

    # ── Hero image (WordPress-hosted) ──────────────────────────────────────
    if hero_media and hero_media.get("URL"):
        photographer     = (hero_image or {}).get("photographer", "")
        photographer_url = (hero_image or {}).get("photographer_url", "")
        unsplash_url     = (hero_image or {}).get("unsplash_url", "")
        alt              = (hero_image or {}).get("alt", "Product review image")
        hid              = hero_media.get("ID")
        caption          = (
            f'<figcaption class="wp-element-caption">'
            f'Photo by <a href="{photographer_url}?utm_source=mavrino&utm_medium=referral" rel="nofollow">{photographer}</a> '
            f'on <a href="{unsplash_url}?utm_source=mavrino&utm_medium=referral" rel="nofollow">Unsplash</a>'
            f'</figcaption>' if photographer else ''
        )
        parts.append(
            f'<!-- wp:image {{"id":{hid},"sizeSlug":"large","align":"wide"}} -->\n'
            f'<figure class="wp-block-image alignwide size-large">'
            f'<img src="{hero_media["URL"]}" alt="{alt}" class="wp-image-{hid}"/>'
            f'{caption}</figure>\n'
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
        # Don't list the winner again — it already has the Top Pick callout above
        if asin and asin == winner_asin:
            continue
        product = products_by_asin.get(asin, {})
        title   = product.get("title", asin)
        price   = product.get("price", 0)
        rating  = product.get("rating", 0)
        reviews = product.get("review_count", 0)
        media   = image_map.get(asin) or {}
        img_url = media.get("URL", "")
        img_id  = media.get("ID")
        aff_url = amazon_search_url(title)
        stars   = "★" * int(rating) + ("½" if rating % 1 >= 0.5 else "")

        card = (
            f'<!-- wp:group {{"className":"product-card","style":{{"border":{{"width":"1px","color":"#e4e0d8","radius":"8px"}},"spacing":{{"padding":{{"all":"20px"}}}}}}}} -->\n'
            f'<div class="wp-block-group product-card" style="border:1px solid #e4e0d8;border-radius:8px;padding:20px">\n'
        )

        # Product image (WordPress-hosted)
        if img_url:
            card += (
                f'<!-- wp:image {{"id":{img_id},"sizeSlug":"medium","align":"right"}} -->\n'
                f'<figure class="wp-block-image alignright size-medium">'
                f'<a href="{aff_url}" rel="nofollow sponsored" target="_blank">'
                f'<img src="{img_url}" alt="{title}" class="wp-image-{img_id}"/></a></figure>\n'
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


# ── Shared block helpers (used by the comparison + review renderers) ───────────

def _disclosure_block() -> str:
    return (
        '<!-- wp:paragraph {"className":"affiliate-disclosure","style":{"spacing":{"padding":{"all":"12px"}},"color":{"background":"#fff8e6"}}} -->\n'
        '<p class="affiliate-disclosure" style="background-color:#fff8e6;padding:12px">'
        '<strong>Disclosure:</strong> Mavrino earns commissions from qualifying purchases '
        'made through links on this page. This does not affect our recommendations.</p>\n'
        '<!-- /wp:paragraph -->'
    )


def _hero_block(hero_image: dict, hero_media: dict) -> str:
    if not (hero_media and hero_media.get("URL")):
        return ""
    hi  = hero_image or {}
    hid = hero_media.get("ID")
    caption = (
        f'<figcaption class="wp-element-caption">Photo by '
        f'<a href="{hi.get("photographer_url","")}?utm_source=mavrino&utm_medium=referral" rel="nofollow">{hi.get("photographer","")}</a> '
        f'on <a href="{hi.get("unsplash_url","")}?utm_source=mavrino&utm_medium=referral" rel="nofollow">Unsplash</a></figcaption>'
        if hi.get("photographer") else ''
    )
    return (
        f'<!-- wp:image {{"id":{hid},"sizeSlug":"large","align":"wide"}} -->\n'
        f'<figure class="wp-block-image alignwide size-large">'
        f'<img src="{hero_media["URL"]}" alt="{hi.get("alt","Product comparison image")}" class="wp-image-{hid}"/>'
        f'{caption}</figure>\n<!-- /wp:image -->'
    )


def _author_block() -> str:
    author_bio = os.getenv("AUTHOR_BIO", "Mavrino tests home, kitchen, travel and lifestyle products to help US shoppers buy with confidence.")
    return (
        '<!-- wp:group {"className":"author-box","style":{"border":{"width":"1px","color":"#e4e0d8","radius":"8px"},"spacing":{"padding":{"all":"16px"}},"color":{"background":"#f5f2ec"}}} -->\n'
        '<div class="wp-block-group author-box" style="border:1px solid #e4e0d8;border-radius:8px;padding:16px;background-color:#f5f2ec">\n'
        '<!-- wp:paragraph -->\n'
        f'<p><strong>Mavrino Editorial</strong> — {author_bio}</p>\n'
        '<!-- /wp:paragraph -->\n'
        '</div>\n<!-- /wp:group -->'
    )


def _faq_blocks(faq: list[dict]) -> list[str]:
    out = []
    if faq:
        out.append('<!-- wp:heading {"level":2} -->\n<h2>Frequently Asked Questions</h2>\n<!-- /wp:heading -->')
        for item in faq:
            if item.get("q"):
                out.append(f'<!-- wp:heading {{"level":3}} -->\n<h3>{item["q"]}</h3>\n<!-- /wp:heading -->')
            if item.get("a"):
                out.append(f'<!-- wp:paragraph -->\n<p>{item["a"]}</p>\n<!-- /wp:paragraph -->')
    return out


def _intro_blocks(intro: str) -> list[str]:
    return [f'<!-- wp:paragraph -->\n<p>{p.strip()}</p>\n<!-- /wp:paragraph -->'
            for p in (intro or "").split('\n\n') if p.strip()]


def _cta_button(label: str, url: str, bg: str = "#1c1814") -> str:
    return (
        f'<!-- wp:buttons -->\n<div class="wp-block-buttons">\n'
        f'<!-- wp:button {{"style":{{"color":{{"background":"{bg}","text":"#ffffff"}},"border":{{"radius":"6px"}}}}}} -->\n'
        f'<div class="wp-block-button"><a class="wp-block-button__link" href="{url}" rel="nofollow sponsored" target="_blank">{label}</a></div>\n'
        f'<!-- /wp:button -->\n</div>\n<!-- /wp:buttons -->'
    )


def _product_image_block(media: dict, title: str, aff_url: str) -> str:
    if not (media and media.get("URL")):
        return ""
    mid = media.get("ID")
    return (
        f'<!-- wp:image {{"id":{mid},"sizeSlug":"medium","align":"right"}} -->\n'
        f'<figure class="wp-block-image alignright size-medium">'
        f'<a href="{aff_url}" rel="nofollow sponsored" target="_blank">'
        f'<img src="{media["URL"]}" alt="{title}" class="wp-image-{mid}"/></a></figure>\n'
        f'<!-- /wp:image -->'
    )


def build_comparison_content(content: dict, products: list[dict], hero_image: dict = None,
                             hero_media: dict = None, image_map: dict = None) -> str:
    """Render a head-to-head comparison post (X vs Y) as Gutenberg blocks."""
    image_map = image_map or {}
    by_asin   = {p.get("asin"): p for p in products}
    pa = products[0] if len(products) > 0 else {}
    pb = products[1] if len(products) > 1 else {}
    parts = [_disclosure_block()]
    hero = _hero_block(hero_image, hero_media)
    if hero:
        parts.append(hero)
    parts += _intro_blocks(content.get("intro", ""))

    # Recommended pick callout
    winner_asin = content.get("winner", "")
    winner      = by_asin.get(winner_asin, {})
    if winner and content.get("winner_reason"):
        aff = amazon_search_url(winner.get("title", ""))
        parts.append(
            '<!-- wp:group {"className":"top-pick-box","style":{"border":{"width":"2px","color":"#b8431a","radius":"8px"},"spacing":{"padding":{"all":"20px"}},"color":{"background":"#fff8f5"}}} -->\n'
            '<div class="wp-block-group top-pick-box" style="border:2px solid #b8431a;border-radius:8px;padding:20px;background-color:#fff8f5">\n'
            '<!-- wp:paragraph {"style":{"typography":{"fontSize":"12px","fontWeight":"600","letterSpacing":"2px","textTransform":"uppercase"},"color":{"text":"#b8431a"}}} -->\n'
            '<p style="font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#b8431a">⭐ Our Recommendation</p>\n'
            '<!-- /wp:paragraph -->\n'
            f'<!-- wp:heading {{"level":3,"style":{{"typography":{{"fontSize":"18px"}}}}}} -->\n<h3 style="font-size:18px">{winner.get("title","")}</h3>\n<!-- /wp:heading -->\n'
            f'<!-- wp:paragraph -->\n<p>{content.get("winner_reason","")}</p>\n<!-- /wp:paragraph -->\n'
            + _cta_button("Check Price on Amazon →", aff, bg="#b8431a") + '\n'
            '</div>\n<!-- /wp:group -->'
        )

    # Head-to-head table
    rows = content.get("head_to_head", [])
    if rows and pa and pb:
        a_name = pa.get("title", "Product A")[:40]
        b_name = pb.get("title", "Product B")[:40]
        thead  = f'<thead><tr><th>Category</th><th>{a_name}</th><th>{b_name}</th></tr></thead>'
        body_rows = "".join(
            f'<tr><td><strong>{r.get("category","")}</strong></td>'
            f'<td>{r.get("product_a","")}</td><td>{r.get("product_b","")}</td></tr>'
            for r in rows
        )
        parts.append('<!-- wp:heading {"level":2} -->\n<h2>Head-to-Head</h2>\n<!-- /wp:heading -->')
        parts.append(
            '<!-- wp:table {"className":"is-style-stripes"} -->\n'
            f'<figure class="wp-block-table is-style-stripes"><table>{thead}<tbody>{body_rows}</tbody></table></figure>\n'
            '<!-- /wp:table -->'
        )

    # Individual mini-reviews
    for key, prod in [("product_a_review", pa), ("product_b_review", pb)]:
        rev = content.get(key, {})
        if not (rev and prod):
            continue
        asin   = prod.get("asin", "")
        title  = prod.get("title", "")
        aff    = amazon_search_url(title)
        card   = (
            '<!-- wp:group {"className":"product-card","style":{"border":{"width":"1px","color":"#e4e0d8","radius":"8px"},"spacing":{"padding":{"all":"20px"}}}} -->\n'
            '<div class="wp-block-group product-card" style="border:1px solid #e4e0d8;border-radius:8px;padding:20px">\n'
        )
        card += _product_image_block(image_map.get(asin), title, aff) + "\n"
        card += f'<!-- wp:heading {{"level":3}} -->\n<h3>{title}</h3>\n<!-- /wp:heading -->\n'
        if prod.get("price") or prod.get("rating"):
            price_str = f"<strong>${prod.get('price',0):.2f}</strong>" if prod.get("price") else ""
            rate_str  = f"★ {prod.get('rating','')}/5" if prod.get("rating") else ""
            card += f'<!-- wp:paragraph -->\n<p>{price_str}&nbsp;&nbsp;{rate_str}</p>\n<!-- /wp:paragraph -->\n'
        if rev.get("summary"):
            card += f'<!-- wp:paragraph -->\n<p>{rev["summary"]}</p>\n<!-- /wp:paragraph -->\n'
        if rev.get("best_for"):
            card += f'<!-- wp:paragraph -->\n<p>👤 <strong>Best for:</strong> {rev["best_for"]}</p>\n<!-- /wp:paragraph -->\n'
        if rev.get("real_quote"):
            card += f'<!-- wp:quote -->\n<blockquote class="wp-block-quote"><p>{rev["real_quote"]}</p><cite>Verified Amazon buyer</cite></blockquote>\n<!-- /wp:quote -->\n'
        card += _cta_button("Check Price on Amazon →", aff) + "\n"
        card += '</div>\n<!-- /wp:group -->'
        parts.append(card)

    # Verdict
    if content.get("verdict"):
        parts.append('<!-- wp:heading {"level":2} -->\n<h2>The Verdict</h2>\n<!-- /wp:heading -->')
        parts += _intro_blocks(content["verdict"])

    parts += _faq_blocks(content.get("faq", []))
    parts.append(_author_block())
    return "\n\n".join(parts)


def build_review_content(content: dict, products: list[dict], hero_image: dict = None,
                         hero_media: dict = None, image_map: dict = None) -> str:
    """Render a single-product deep review as Gutenberg blocks."""
    image_map = image_map or {}
    prod  = products[0] if products else {}
    asin  = prod.get("asin", "")
    title = prod.get("title", content.get("title", ""))
    aff   = amazon_search_url(title)
    parts = [_disclosure_block()]
    hero = _hero_block(hero_image, hero_media)
    if hero:
        parts.append(hero)
    parts += _intro_blocks(content.get("intro", ""))

    # Quick verdict box
    if content.get("quick_verdict"):
        score = content.get("score", {})
        overall = score.get("overall", "")
        parts.append(
            '<!-- wp:group {"className":"top-pick-box","style":{"border":{"width":"2px","color":"#b8431a","radius":"8px"},"spacing":{"padding":{"all":"20px"}},"color":{"background":"#fff8f5"}}} -->\n'
            '<div class="wp-block-group top-pick-box" style="border:2px solid #b8431a;border-radius:8px;padding:20px;background-color:#fff8f5">\n'
            '<!-- wp:paragraph {"style":{"typography":{"fontSize":"12px","fontWeight":"600","letterSpacing":"2px","textTransform":"uppercase"},"color":{"text":"#b8431a"}}} -->\n'
            f'<p style="font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#b8431a">⭐ Verdict{f" — {overall}/10" if overall else ""}</p>\n'
            '<!-- /wp:paragraph -->\n'
            f'<!-- wp:paragraph -->\n<p>{content["quick_verdict"]}</p>\n<!-- /wp:paragraph -->\n'
            + _cta_button("Check Price on Amazon →", aff, bg="#b8431a") + '\n'
            '</div>\n<!-- /wp:group -->'
        )

    # Like / don't-like columns
    likes  = content.get("what_we_like", [])
    nots   = content.get("what_we_dont", [])
    if likes or nots:
        like_html = "".join(f'<!-- wp:list-item --><li>{x}</li><!-- /wp:list-item -->' for x in likes)
        not_html  = "".join(f'<!-- wp:list-item --><li>{x}</li><!-- /wp:list-item -->' for x in nots)
        parts.append(
            '<!-- wp:columns -->\n<div class="wp-block-columns">\n'
            '<!-- wp:column -->\n<div class="wp-block-column">\n'
            '<!-- wp:heading {"level":3} -->\n<h3>✅ What we like</h3>\n<!-- /wp:heading -->\n'
            f'<!-- wp:list -->\n<ul class="wp-block-list">{like_html}</ul>\n<!-- /wp:list -->\n'
            '</div>\n<!-- /wp:column -->\n'
            '<!-- wp:column -->\n<div class="wp-block-column">\n'
            '<!-- wp:heading {"level":3} -->\n<h3>⚠️ What to consider</h3>\n<!-- /wp:heading -->\n'
            f'<!-- wp:list -->\n<ul class="wp-block-list">{not_html}</ul>\n<!-- /wp:list -->\n'
            '</div>\n<!-- /wp:column -->\n</div>\n<!-- /wp:columns -->'
        )

    # Sections
    for sec in content.get("sections", []):
        if sec.get("heading"):
            parts.append(f'<!-- wp:heading {{"level":2}} -->\n<h2>{sec["heading"]}</h2>\n<!-- /wp:heading -->')
        if sec.get("content"):
            parts += _intro_blocks(sec["content"])

    # Real owner quotes
    for q in content.get("real_owner_say", []):
        if q.get("quote"):
            stars = "★" * int(q.get("stars", 0))
            parts.append(f'<!-- wp:quote -->\n<blockquote class="wp-block-quote"><p>{q["quote"]}</p><cite>{stars} Verified Amazon buyer</cite></blockquote>\n<!-- /wp:quote -->')

    # Who should / shouldn't buy
    if content.get("who_should_buy"):
        parts.append(f'<!-- wp:paragraph -->\n<p>✅ <strong>Buy it if:</strong> {content["who_should_buy"]}</p>\n<!-- /wp:paragraph -->')
    if content.get("who_should_skip"):
        parts.append(f'<!-- wp:paragraph -->\n<p>⚠️ <strong>Skip it if:</strong> {content["who_should_skip"]}</p>\n<!-- /wp:paragraph -->')

    if content.get("verdict"):
        parts.append('<!-- wp:heading {"level":2} -->\n<h2>Bottom Line</h2>\n<!-- /wp:heading -->')
        parts += _intro_blocks(content["verdict"])
        parts.append(_cta_button("Check Price on Amazon →", aff, bg="#b8431a"))

    parts += _faq_blocks(content.get("faq", []))
    parts.append(_author_block())
    return "\n\n".join(parts)


def qa_readback(post_id, token: str) -> list[str]:
    """Read a published post back and flag obvious defects (leaked schema, thin
    content, no images). Returns a list of issue strings (empty = clean)."""
    issues = []
    data = wp_request("GET", f"posts/{post_id}", token)
    if not data:
        return ["could not read post back"]
    content = data.get("content", "") or ""
    words = len(re.sub(r"<[^>]+>", " ", content).split())
    if '"@context"' in content or "ld+json" in content:
        issues.append("schema/JSON leaked into body")
    if words < 350:
        issues.append(f"thin content ({words} words)")
    if "wp-content/uploads" not in content and "<img" not in content:
        issues.append("no images")
    if issues:
        print(f"  [qa] WARNING post {post_id}: {'; '.join(issues)}")
    else:
        print(f"  [qa] ok ({words} words)")
    return issues


def publish_to_wordpress(content: dict, products: list[dict], keyword_data: dict) -> dict | None:
    """Publish a post to WordPress with taxonomy, SEO, images."""
    token = get_access_token()
    if not token:
        return None

    keyword = content.get("keyword", "")
    title   = content.get("title", keyword)

    try:
        import image_fetcher as imf
    except Exception:
        imf = None

    # ── Hero image: fetch from Unsplash, then sideload into WP media library ──
    hero_image = hero_media = None
    if imf:
        try:
            hero_image = imf.get_hero_image(keyword)
            if hero_image and hero_image.get("url"):
                hero_media = upload_media_from_url(hero_image["url"], token, f"{keyword}-hero")
                if hero_media:
                    print(f"  [images] Hero uploaded → media #{hero_media['ID']}")
        except Exception as e:
            print(f"  [images] Could not fetch/upload hero: {e}")

    # ── Product images: ONLY use a real product image (Amazon). We deliberately
    #    do NOT fall back to a generic Unsplash stock photo per product — stock
    #    images misrepresent specific models (e.g. a deep fryer for an air fryer).
    #    Today the seed image_urls are dead, so cards show no photo; once the
    #    PA-API supplies real images per ASIN, they appear automatically here.
    image_map: dict = {}
    for p in products:
        asin = p.get("asin")
        src  = p.get("image_url", "")
        if src:
            media = upload_media_from_url(src, token, f"{asin or _slug(p.get('title',''))}")
            if media:
                image_map[asin] = media
    if image_map:
        print(f"  [images] {len(image_map)} real product image(s) uploaded")

    # Build content — dispatch on post format (comparison/review have their own
    # JSON shapes; everything else uses the roundup/listicle renderer).
    post_type = content.get("post_type", keyword_data.get("post_type", "roundup"))
    if post_type == "comparison" and content.get("head_to_head"):
        wp_content = build_comparison_content(content, products, hero_image, hero_media, image_map)
    elif post_type == "review" and content.get("sections"):
        wp_content = build_review_content(content, products, hero_image, hero_media, image_map)
    else:
        wp_content = build_wp_content(content, products, hero_image, hero_media, image_map)

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

    # Set the post's Featured Image (shows in Kadence's blog/archive/related
    # thumbnails) by the uploaded media's attachment ID. Passing a raw external
    # URL here is silently ignored by WordPress.com — it requires a media ID.
    if hero_media and hero_media.get("ID"):
        post_data["featured_image"] = hero_media["ID"]

    print(f"  [wp] Publishing '{title[:55]}...'")
    result = wp_request("POST", "posts/new", token, post_data)

    if result and result.get("ID"):
        post_id  = result["ID"]
        post_url = result.get("URL", "")
        print(f"  [wp] Published: {post_url} (ID: {post_id})")

        # Guarantee pingbacks are closed — the create-time flag is unreliable, so
        # confirm via the nested-discussion form (prevents self-pingback comments).
        wp_request("POST", f"posts/{post_id}", token, {"discussion": {"pings_open": False}})

        # QA: read the post back and flag obvious defects (leaked schema, thin, etc.)
        qa_readback(post_id, token)

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
