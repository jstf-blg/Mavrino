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

from url_safety import is_safe_public_url
from safe_io import write_json, load_json

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


def _num(x, default: float = 0.0) -> float:
    """Coerce external product fields (rating/price) to a number.

    Scraped data sometimes delivers ratings/prices as strings ("4.5") or junk;
    int("4.5") / "4.5" % 1 would crash a whole post build. Always go through here.
    """
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


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
    if not is_safe_public_url(image_url):
        print(f"  [wp] Refused unsafe image URL ({filename}): {image_url[:80]}")
        _MEDIA_CACHE[image_url] = None
        return None

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
    if not is_safe_public_url(url):
        _IMG_OK_CACHE[url] = False
        return False
    ok = False
    try:
        r = requests.head(url, timeout=8, allow_redirects=True)
        ok = (r.status_code == 200)
    except Exception:
        ok = False
    _IMG_OK_CACHE[url] = ok
    return ok


def build_wp_content(content: dict, products: list[dict], hero_image: dict = None,
                     hero_media: dict = None, image_map: dict = None,
                     accent: str = None, layout: dict = None) -> str:
    """Build Gutenberg block HTML from generated content.

    Variation engine: `accent` (per-vertical colour) and `layout` (a recipe that
    reorders/selects the middle sections) make posts look and read differently at
    scale. Falls back to a slug-seeded layout + default accent when not supplied.
    """
    products_by_asin = {p.get("asin"): p for p in products}
    image_map = image_map or {}
    try:
        import variations as _var
    except Exception:
        _var = None
    accent = accent or (_var.DEFAULT_ACCENT if _var else "#b8431a")
    if layout is None:
        layout = (_var.layout_for(content.get("keyword", "")) if _var
                  else {"sections": ["intro", "top_pick", "cards", "buying_guide", "faq"]})
    winner_asin = content.get("winner_asin", "")

    # ── Section builders (assembled later per the layout recipe) ───────────
    def sec_intro():
        return [f'<!-- wp:paragraph -->\n<p>{p.strip()}</p>\n<!-- /wp:paragraph -->'
                for p in content.get("intro", "").split('\n\n') if p.strip()]

    def sec_top_pick():
        wv = content.get("winner_verdict", "")
        if not wv:
            return []
        wprod = products_by_asin.get(winner_asin, {})
        title = wprod.get("title", ""); price = _num(wprod.get("price", 0)); rating = _num(wprod.get("rating", 0))
        url   = amazon_search_url(title) if title else ""; stars = "★" * int(rating)
        out = (
            f'<!-- wp:group {{"className":"top-pick-box","style":{{"border":{{"width":"2px","color":"{accent}","radius":"8px"}},"spacing":{{"padding":{{"all":"20px"}}}},"color":{{"background":"#fff8f5"}}}}}} -->\n'
            f'<div class="wp-block-group top-pick-box" style="border:2px solid {accent};border-radius:8px;padding:20px;background-color:#fff8f5">\n'
            f'<!-- wp:paragraph {{"style":{{"typography":{{"fontSize":"12px","fontWeight":"600","letterSpacing":"2px","textTransform":"uppercase"}},"color":{{"text":"{accent}"}}}}}} -->\n'
            f'<p style="font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:{accent}">⭐ Our Top Pick</p>\n<!-- /wp:paragraph -->\n'
            f'<!-- wp:heading {{"level":3,"style":{{"typography":{{"fontSize":"18px"}}}}}} -->\n<h3 style="font-size:18px">{title}</h3>\n<!-- /wp:heading -->\n'
        )
        # COMPETENCE cue: a confident, decisive one-line pitch up top.
        pitch = (content.get("winner_pitch") or "").strip()
        if pitch:
            out += (f'<!-- wp:paragraph {{"style":{{"typography":{{"fontSize":"17px","fontWeight":"600"}}}}}} -->\n'
                    f'<p style="font-size:17px;font-weight:600">{pitch}</p>\n<!-- /wp:paragraph -->\n')
        out += f'<!-- wp:paragraph -->\n<p>{wv}</p>\n<!-- /wp:paragraph -->\n'
        # WARMTH cue: one honest trade-off — this is what makes the confidence believable.
        caveat = (content.get("winner_caveat") or "").strip()
        if caveat:
            out += (f'<!-- wp:paragraph -->\n<p>⚖️ <strong>The honest trade-off:</strong> {caveat}</p>\n<!-- /wp:paragraph -->\n')
        badge = _score_badge(wprod.get("mavrino_score"), accent)
        if badge:
            out += badge + "\n"
        if price:
            out += (f'<!-- wp:paragraph -->\n<p><strong>${price:.2f}</strong>'
                    f'{" &nbsp; " + stars if stars else ""}{" " + str(rating) + "/5" if rating else ""}</p>\n<!-- /wp:paragraph -->\n')
        out += _trust_block(wprod, peer_count=len(products)) + "\n"
        if url:
            out += _cta_button("Check today’s price on Amazon →", url, bg=accent) + "\n"
        out += '</div>\n<!-- /wp:group -->'
        return [out]

    def sec_cards():
        cards = []
        for item in content.get("products", []):
            asin = item.get("asin", "")
            if asin and asin == winner_asin:
                continue
            product = products_by_asin.get(asin, {})
            title   = product.get("title", asin)
            price   = _num(product.get("price", 0)); rating = _num(product.get("rating", 0))
            reviews = product.get("review_count", 0)
            media   = image_map.get(asin) or {}
            img_url = media.get("URL", "")
            aff_url = amazon_search_url(title)
            stars   = "★" * int(rating) + ("½" if rating % 1 >= 0.5 else "")
            card = ('<!-- wp:group {"className":"product-card","style":{"border":{"width":"1px","color":"#e4e0d8","radius":"8px"},"spacing":{"padding":{"all":"20px"}}}} -->\n'
                    '<div class="wp-block-group product-card" style="border:1px solid #e4e0d8;border-radius:8px;padding:20px">\n')
            card += _product_image_block(media, title, aff_url) + ("\n" if img_url else "")
            if item.get("heading"):
                card += (f'<!-- wp:paragraph {{"style":{{"typography":{{"fontSize":"11px","fontWeight":"700","letterSpacing":"2px","textTransform":"uppercase"}},"color":{{"text":"{accent}"}}}}}} -->\n'
                         f'<p style="font-size:11px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:{accent}">{item["heading"]}</p>\n<!-- /wp:paragraph -->\n')
            card += f'<!-- wp:heading {{"level":3}} -->\n<h3>{title}</h3>\n<!-- /wp:heading -->\n'
            if price or rating:
                price_str  = f"<strong>${price:.2f}</strong>" if price else ""
                rating_str = f"{stars} {rating}/5 ({reviews:,} reviews)" if rating else ""
                card += f'<!-- wp:paragraph -->\n<p>{price_str}{"&nbsp;&nbsp;" if price_str and rating_str else ""}{rating_str}</p>\n<!-- /wp:paragraph -->\n'
            badge = _score_badge(product.get("mavrino_score"), accent)
            if badge:
                card += badge + "\n"
            if item.get("verdict"):
                card += f'<!-- wp:paragraph -->\n<p>{item["verdict"]}</p>\n<!-- /wp:paragraph -->\n'
            if item.get("who_its_for"):
                card += f'<!-- wp:paragraph -->\n<p>\U0001f464 <strong>Best for:</strong> {item["who_its_for"]}</p>\n<!-- /wp:paragraph -->\n'
            if item.get("not_for"):
                card += f'<!-- wp:paragraph -->\n<p>\U0001f6ab <strong>Skip it if:</strong> {item["not_for"]}</p>\n<!-- /wp:paragraph -->\n'
            if item.get("main_pro") or item.get("main_con"):
                card += ('<!-- wp:columns -->\n<div class="wp-block-columns">\n'
                         '<!-- wp:column -->\n<div class="wp-block-column">\n'
                         f'<!-- wp:paragraph -->\n<p>✅ <strong>Pro:</strong> {item.get("main_pro","")}</p>\n<!-- /wp:paragraph -->\n'
                         '</div>\n<!-- /wp:column -->\n<!-- wp:column -->\n<div class="wp-block-column">\n'
                         f'<!-- wp:paragraph -->\n<p>⚠️ <strong>Consider:</strong> {item.get("main_con","")}</p>\n<!-- /wp:paragraph -->\n'
                         '</div>\n<!-- /wp:column -->\n</div>\n<!-- /wp:columns -->\n')
            if item.get("quote"):
                card += f'<!-- wp:quote -->\n<blockquote class="wp-block-quote"><p>{item["quote"]}</p><cite>Verified Amazon buyer</cite></blockquote>\n<!-- /wp:quote -->\n'
            card += _cta_button("Check price on Amazon →", aff_url) + "\n"
            card += '</div>\n<!-- /wp:group -->'
            cards.append(card)
        return cards

    def sec_buying_guide():
        if not content.get("buying_guide"):
            return []
        out = ['<!-- wp:heading {"level":2} -->\n<h2>How to Choose</h2>\n<!-- /wp:heading -->']
        out += [f'<!-- wp:paragraph -->\n<p>{p.strip()}</p>\n<!-- /wp:paragraph -->'
                for p in content["buying_guide"].split('\n\n') if p.strip()]
        return out

    def sec_faq():
        return _faq_blocks(content.get("faq", []))

    def sec_comparison_table():
        items = content.get("products", [])
        if len(items) < 2:
            return []
        rows = ""
        for it in items:
            p = products_by_asin.get(it.get("asin"), {})
            if not p:
                continue
            best = (it.get("heading") or it.get("who_its_for", "") or "")[:40]
            sc   = p.get("mavrino_score", "")
            rows += (f'<tr><td><strong>{p.get("title","")[:42]}</strong></td>'
                     f'<td><strong>{sc}/10</strong></td>'
                     f'<td>${p.get("price",0):.0f}</td><td>{p.get("rating","")}/5</td><td>{best}</td></tr>')
        if not rows:
            return []
        return ['<!-- wp:heading {"level":2} -->\n<h2>At a Glance</h2>\n<!-- /wp:heading -->',
                '<!-- wp:table {"className":"is-style-stripes"} -->\n'
                '<figure class="wp-block-table is-style-stripes"><table>'
                '<thead><tr><th>Product</th><th>Mavrino Score</th><th>Price</th><th>Rating</th><th>Best for</th></tr></thead>'
                f'<tbody>{rows}</tbody></table></figure>\n<!-- /wp:table -->']

    def sec_key_takeaways():
        kt = [t for t in (content.get("key_takeaways") or []) if str(t).strip()]
        if not kt:
            return []
        items = "".join(f"<li>{t}</li>" for t in kt)
        return [
            f'<!-- wp:group {{"className":"key-takeaways","style":{{"spacing":{{"padding":{{"all":"18px"}}}},"border":{{"left":{{"width":"4px","color":"{accent}"}}}},"color":{{"background":"#faf7f3"}}}}}} -->\n'
            f'<div class="wp-block-group key-takeaways" style="padding:18px;border-left:4px solid {accent};background-color:#faf7f3">\n'
            f'<!-- wp:paragraph {{"style":{{"typography":{{"fontSize":"12px","fontWeight":"700","letterSpacing":"2px","textTransform":"uppercase"}},"color":{{"text":"{accent}"}}}}}} -->\n'
            f'<p style="font-size:12px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:{accent}">Key Takeaways</p>\n<!-- /wp:paragraph -->\n'
            f'<!-- wp:list -->\n<ul class="wp-block-list">{items}</ul>\n<!-- /wp:list -->\n'
            f'</div>\n<!-- /wp:group -->'
        ]

    def sec_bottom_line():
        bl = (content.get("bottom_line") or "").strip()
        if not bl:
            return []
        out = ['<!-- wp:heading {"level":2} -->\n<h2>The Bottom Line</h2>\n<!-- /wp:heading -->']
        out += [f'<!-- wp:paragraph -->\n<p>{p.strip()}</p>\n<!-- /wp:paragraph -->'
                for p in bl.split('\n\n') if p.strip()]
        return out

    builders = {"intro": sec_intro, "top_pick": sec_top_pick, "cards": sec_cards,
                "buying_guide": sec_buying_guide, "faq": sec_faq, "comparison_table": sec_comparison_table,
                "key_takeaways": sec_key_takeaways, "bottom_line": sec_bottom_line}

    # ── Assemble: disclosure + hero, middle sections per recipe, author box ─
    # Ad slots (invisible .mavrino-ad containers) are placed after the intro and
    # mid-content so an ad plugin / AdSense can fill them — see wordpress/mavrino-schema.php.
    parts = [_disclosure_block()]
    hero = _hero_block(hero_image, hero_media)
    if hero:
        parts.append(hero)
    parts.append(_updated_line())
    middle = list(layout.get("sections", ["intro", "key_takeaways", "top_pick", "cards", "buying_guide", "bottom_line", "faq"]))
    # Ensure the deep-content sections render even when a variation supplies its own recipe.
    if "key_takeaways" not in middle:
        middle.insert(middle.index("intro") + 1 if "intro" in middle else 0, "key_takeaways")
    if "bottom_line" not in middle:
        middle.insert(middle.index("faq") if "faq" in middle else len(middle), "bottom_line")
    for i, key in enumerate(middle):
        fn = builders.get(key)
        if fn:
            parts.extend(fn())
        if i == 0:
            parts.append(_ad_slot("in-content-1"))            # ad after the intro
        elif i == len(middle) - 2 and len(middle) > 2:
            parts.append(_ad_slot("in-content-2"))            # ad before the final section
    parts.append(_subscribe_block())
    parts.append(_author_block(content.get("persona")))
    return "\n\n".join(parts)


# ── Shared block helpers (used by the comparison + review renderers) ───────────

def _score_badge(score, accent: str = "#b8431a") -> str:
    """Render the proprietary Mavrino Score as a coloured badge line."""
    if not score:
        return ""
    try:
        import scoring
        lab = scoring.score_label(float(score))
    except Exception:
        lab = ""
    return (f'<!-- wp:paragraph {{"style":{{"typography":{{"fontSize":"15px"}}}}}} -->\n'
            f'<p style="font-size:15px"><strong style="color:{accent}">★ Mavrino Score: {score}/10</strong>'
            f'{" · " + lab if lab else ""}</p>\n<!-- /wp:paragraph -->')


def _ad_slot(slot: str = "in-content") -> str:
    """A reserved ad-inventory container. The min-height reserves layout space so a
    late-loading ad can't shift the page (a 2025 ad-quality + Core Web Vitals
    requirement — unreserved slots suffer ~50% lower viewability + ranking penalties).
    Plain <div> survives WP.com sanitisation, so an ad plugin / AdSense Auto-ads fills it."""
    return (f'<!-- wp:html -->\n'
            f'<div class="mavrino-ad" data-ad-slot="{slot}" '
            f'style="margin:24px auto;min-height:250px;text-align:center"></div>\n'
            f'<!-- /wp:html -->')


def _subscribe_block() -> str:
    """Email-capture form (Jetpack subscription shortcode — renders reliably without
    needing the block picker; requires Jetpack Subscriptions enabled, which it is)."""
    return ('<!-- wp:shortcode -->\n'
            '[jetpack_subscription_form title="Get our weekly picks" '
            'subscribe_text="New, data-ranked buying guides straight to your inbox. No spam." '
            'subscribe_button="Subscribe"]\n'
            '<!-- /wp:shortcode -->')


def _updated_line() -> str:
    """Freshness signal — 'Last updated' line. The refresh job revisits prices/ratings,
    so this is a genuine recency marker (a ranking signal AI sites usually neglect)."""
    return (f'<!-- wp:paragraph {{"style":{{"typography":{{"fontSize":"13px"}}}}}} -->\n'
            f'<p style="font-size:13px;color:#8a8480"><em>Last updated {datetime.utcnow():%B %Y} · '
            f'prices and ratings re-checked regularly.</em></p>\n<!-- /wp:paragraph -->')


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


def _author_block(persona: dict = None) -> str:
    persona = persona or {}
    name = persona.get("name", "Mavrino Editorial")
    bio  = persona.get("bio") or os.getenv("AUTHOR_BIO", "Mavrino tests home, kitchen, travel and lifestyle products to help US shoppers buy with confidence.")
    label = f"By {name}" if persona.get("name") else name
    return (
        '<!-- wp:group {"className":"author-box","style":{"border":{"width":"1px","color":"#e4e0d8","radius":"8px"},"spacing":{"padding":{"all":"16px"}},"color":{"background":"#f5f2ec"}}} -->\n'
        '<div class="wp-block-group author-box" style="border:1px solid #e4e0d8;border-radius:8px;padding:16px;background-color:#f5f2ec">\n'
        '<!-- wp:paragraph -->\n'
        f'<p><strong>{label}</strong> — {bio}</p>\n'
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


def _trust_block(product: dict, peer_count: int = 0) -> str:
    """'Why you can trust this pick' cue stack next to the CTA — rigour
    (competence) plus candour (warmth) to de-risk the click. Lines are only
    shown when the underlying data exists, so it never fabricates signals."""
    signals = []
    if peer_count and peer_count >= 2:
        signals.append(f"Ranked against {peer_count} models on price, rating &amp; real reviews")
    bits = []
    score = product.get("mavrino_score")
    if score:
        bits.append(f"Mavrino Score {score}/10")
    rc = int(product.get("review_count", 0) or 0)
    if rc:
        bits.append(f"{rc:,} verified reviews analyzed")
    if bits:
        signals.append(" · ".join(bits))
    signals.append("Independent — we may earn a commission, but it never sways the ranking")
    items = "".join(f"<li>✓ {s}</li>" for s in signals)
    return (
        '<!-- wp:list {"className":"trust-signals"} -->\n'
        f'<ul class="wp-block-list trust-signals" style="list-style:none;margin:10px 0;padding:0;'
        f'font-size:14px;line-height:1.7;color:#4a4a4a">{items}</ul>\n'
        '<!-- /wp:list -->'
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
        box = (
            '<!-- wp:group {"className":"top-pick-box","style":{"border":{"width":"2px","color":"#b8431a","radius":"8px"},"spacing":{"padding":{"all":"20px"}},"color":{"background":"#fff8f5"}}} -->\n'
            '<div class="wp-block-group top-pick-box" style="border:2px solid #b8431a;border-radius:8px;padding:20px;background-color:#fff8f5">\n'
            '<!-- wp:paragraph {"style":{"typography":{"fontSize":"12px","fontWeight":"600","letterSpacing":"2px","textTransform":"uppercase"},"color":{"text":"#b8431a"}}} -->\n'
            '<p style="font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#b8431a">⭐ Our Recommendation</p>\n'
            '<!-- /wp:paragraph -->\n'
            f'<!-- wp:heading {{"level":3,"style":{{"typography":{{"fontSize":"18px"}}}}}} -->\n<h3 style="font-size:18px">{winner.get("title","")}</h3>\n<!-- /wp:heading -->\n'
        )
        pitch = (content.get("winner_pitch") or "").strip()
        if pitch:
            box += (f'<!-- wp:paragraph {{"style":{{"typography":{{"fontSize":"17px","fontWeight":"600"}}}}}} -->\n'
                    f'<p style="font-size:17px;font-weight:600">{pitch}</p>\n<!-- /wp:paragraph -->\n')
        box += f'<!-- wp:paragraph -->\n<p>{content.get("winner_reason","")}</p>\n<!-- /wp:paragraph -->\n'
        caveat = (content.get("winner_caveat") or "").strip()
        if caveat:
            box += f'<!-- wp:paragraph -->\n<p>⚖️ <strong>Pick the other one if:</strong> {caveat}</p>\n<!-- /wp:paragraph -->\n'
        box += _trust_block(winner, peer_count=2) + "\n"
        box += _cta_button("Check Price on Amazon →", aff, bg="#b8431a") + "\n"
        box += '</div>\n<!-- /wp:group -->'
        parts.append(box)

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
    parts.append(_subscribe_block())
    parts.append(_author_block(content.get("persona")))
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
        box = (
            '<!-- wp:group {"className":"top-pick-box","style":{"border":{"width":"2px","color":"#b8431a","radius":"8px"},"spacing":{"padding":{"all":"20px"}},"color":{"background":"#fff8f5"}}} -->\n'
            '<div class="wp-block-group top-pick-box" style="border:2px solid #b8431a;border-radius:8px;padding:20px;background-color:#fff8f5">\n'
            '<!-- wp:paragraph {"style":{"typography":{"fontSize":"12px","fontWeight":"600","letterSpacing":"2px","textTransform":"uppercase"},"color":{"text":"#b8431a"}}} -->\n'
            f'<p style="font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#b8431a">⭐ Verdict{f" — {overall}/10" if overall else ""}</p>\n'
            '<!-- /wp:paragraph -->\n'
            f'<!-- wp:paragraph -->\n<p>{content["quick_verdict"]}</p>\n<!-- /wp:paragraph -->\n'
        )
        vcaveat = (content.get("verdict_caveat") or "").strip()
        if vcaveat:
            box += f'<!-- wp:paragraph -->\n<p>⚖️ <strong>The honest trade-off:</strong> {vcaveat}</p>\n<!-- /wp:paragraph -->\n'
        box += _trust_block(prod, peer_count=0) + "\n"
        box += _cta_button("Check Price on Amazon →", aff, bg="#b8431a") + "\n"
        box += '</div>\n<!-- /wp:group -->'
        parts.append(box)

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
            stars = "★" * int(_num(q.get("stars", 0)))
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
    parts.append(_subscribe_block())
    parts.append(_author_block(content.get("persona")))
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


def publish_to_wordpress(content: dict, products: list[dict], keyword_data: dict,
                         update_post_id: int = None, schedule_date: str = None) -> dict | None:
    """Publish a post to WordPress with taxonomy, SEO, images.

    If update_post_id is given, the existing post is updated in place (same URL)
    instead of creating a new one — used to regenerate a post without changing it.
    If schedule_date (ISO 8601) is given, the post is created as status=future and
    WordPress auto-publishes it at that time — used by the hourly batch scheduler.
    """
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

    # Per-vertical accent colour so each category looks distinct.
    try:
        import variations as _var
        accent = _var.accent_for(_var.vertical_for_keyword(keyword))
    except Exception:
        accent = None

    # Build content — dispatch on post format (comparison/review have their own
    # JSON shapes; everything else uses the roundup/listicle renderer).
    post_type = content.get("post_type", keyword_data.get("post_type", "roundup"))
    if post_type == "comparison" and content.get("head_to_head"):
        wp_content = build_comparison_content(content, products, hero_image, hero_media, image_map)
    elif post_type == "review" and content.get("sections"):
        wp_content = build_review_content(content, products, hero_image, hero_media, image_map)
    else:
        wp_content = build_wp_content(content, products, hero_image, hero_media, image_map, accent=accent)

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
        "status":     "future" if schedule_date else "publish",
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
    if schedule_date:
        post_data["date"] = schedule_date   # ISO 8601 → WP auto-publishes at this time

    endpoint = f"posts/{update_post_id}" if update_post_id else "posts/new"
    print(f"  [wp] {'Updating' if update_post_id else 'Publishing'} '{title[:55]}...'")
    result = wp_request("POST", endpoint, token, post_data)

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
    log = load_json(WP_LOG_FILE, []) or []
    log.append(entry)
    write_json(WP_LOG_FILE, log[-1000:])


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
