"""
build_pillars.py — generate/refresh topical "pillar" pages, one per vertical.
─────────────────────────────────────────────────────────────────────────────
Each pillar is a hub page that introduces the vertical, explains the Mavrino
Score, and links down to every guide in its subcategories (pillar -> cluster).
The primary nav points each vertical at its pillar. Re-run anytime to refresh
the links as new guides publish (safe to run repeatedly).
"""

import sys, html
from pathlib import Path
import requests
sys.path.insert(0, "pipeline")
from dotenv import load_dotenv; load_dotenv()
import wp_publisher as w
import image_fetcher as imf
from safe_io import write_json, load_json

t = w.get_access_token(); s = w.resolve_site(t); h = {"Authorization": f"Bearer {t}"}
B = "https://public-api.wordpress.com/rest/v1.1"
SITE = "https://mavrino.com"

# A representative hero image per vertical (Unsplash search term).
HERO_TERMS = {
    "Kitchen":            "modern kitchen appliances countertop",
    "Home":               "cozy modern living room interior",
    "Travel":             "travel luggage suitcase airport",
    "Home Office":        "home office desk workspace setup",
    "Fitness & Wellness": "home gym fitness equipment",
    "Outdoors":           "camping outdoors gear nature",
}
HERO_CACHE_FILE = Path("config/pillar_heroes.json")


def get_hero(vert: str, vslug: str, cache: dict) -> dict | None:
    """Return {id,url,credit} for the vertical's hero, sideloaded to the media
    library. Cached per vslug so re-runs reuse the same uploaded image."""
    if cache.get(vslug, {}).get("id"):
        return cache[vslug]
    pool = imf._fetch_pool(HERO_TERMS.get(vert, vert), "landscape")
    img = pool[0] if pool else None
    if not img:
        print(f"    (no Unsplash hero for {vert})")
        return None
    media = w.upload_media_from_url(img["url"], t, filename=f"{vslug}-hero")
    if not media or not media.get("ID"):
        print(f"    (hero upload failed for {vert})")
        return None
    rec = {"id": media["ID"], "url": media["URL"], "credit": imf.build_image_credit(img)}
    cache[vslug] = rec
    write_json(HERO_CACHE_FILE, cache)
    return rec


def hero_cover(vert: str, media: dict) -> str:
    url, mid = media["url"], media["id"]
    alt = html.escape(f"{vert} buying guides")
    return (
        f'<!-- wp:cover {{"url":"{url}","id":{mid},"dimRatio":45,"overlayColor":"black",'
        f'"minHeight":340,"align":"full"}} -->\n'
        f'<div class="wp-block-cover alignfull" style="min-height:340px">'
        f'<img class="wp-block-cover__image-background wp-image-{mid}" alt="{alt}" src="{url}" data-object-fit="cover"/>'
        f'<span aria-hidden="true" class="wp-block-cover__background has-black-background-color '
        f'has-background-dim-40 has-background-dim"></span>'
        f'<div class="wp-block-cover__inner-container">\n'
        f'<!-- wp:heading {{"textAlign":"center","level":1,"textColor":"white"}} -->\n'
        f'<h1 class="wp-block-heading has-text-align-center has-white-color has-text-color">{html.escape(vert)}</h1>\n'
        f'<!-- /wp:heading -->\n'
        f'</div></div>\n<!-- /wp:cover -->'
    )

VERTICALS = {
    "Kitchen": ("kitchen", ["Air Fryers", "Blenders", "Coffee Makers", "Electric Kettles", "Food Processors", "Stand Mixers", "Toaster Ovens"]),
    # NOTE: slug is "home-appliances" NOT "home" — "home" is the front page's slug (page 40);
    # using it here would overwrite the magazine homepage. Never reuse the front-page slug.
    "Home": ("home-appliances", ["Robot Vacuums", "Air Purifiers", "Stick Vacuums", "Humidifiers", "Space Heaters"]),
    "Travel": ("travel", ["Luggage", "Travel Backpacks", "Packing Cubes", "Portable Chargers", "Travel Pillows"]),
    "Home Office": ("home-office", ["Standing Desks", "Office Chairs", "Monitors", "Mechanical Keyboards", "Webcams"]),
    "Fitness & Wellness": ("fitness-wellness", ["Massage Guns", "Fitness Trackers", "Smart Scales", "Yoga Mats", "Adjustable Dumbbells"]),
    "Outdoors": ("outdoors", ["Coolers", "Tents", "Camping Chairs", "Portable Grills", "Sleeping Bags"]),
}

cats = requests.get(f"{B}/sites/{s}/categories", headers=h, params={"number": 200}, timeout=25).json().get("categories", [])
slug_by_name = {html.unescape(c["name"]).lower(): c["slug"] for c in cats}
pages = requests.get(f"{B}/sites/{s}/posts", headers=h, params={"type": "page", "number": 50}, timeout=20).json().get("posts", [])
page_id = {p["slug"]: p["ID"] for p in pages}

# Hard guard: never overwrite the site's static front page (the magazine homepage).
_settings = requests.get(f"{B}/sites/{s}/settings", headers=h, timeout=20).json().get("settings", {})
FRONT_PAGE_ID = _settings.get("page_on_front")
page_id = {slug: pid for slug, pid in page_id.items() if pid != FRONT_PAGE_ID}
print(f"front page id (protected): {FRONT_PAGE_ID}")


def posts_in(cat_slug):
    if not cat_slug:
        return []
    d = requests.get(f"{B}/sites/{s}/posts", headers=h,
                     params={"category": cat_slug, "number": 50, "fields": "ID,title,URL"}, timeout=20).json()
    return d.get("posts", [])


def para(x): return f"<!-- wp:paragraph -->\n<p>{x}</p>\n<!-- /wp:paragraph -->"
def head(x, l=2): return f'<!-- wp:heading {{"level":{l}}} -->\n<h{l}>{x}</h{l}>\n<!-- /wp:heading -->'


hero_cache = load_json(HERO_CACHE_FILE, {}) or {}
pillar_pages = {}
for vert, (vslug, subs) in VERTICALS.items():
    sub_list = ", ".join(subs[:-1]) + " and " + subs[-1]
    hero = get_hero(vert, vslug, hero_cache)
    blocks = []
    if hero:
        blocks.append(hero_cover(vert, hero))
    blocks += [
        para(f"Welcome to Mavrino's {vert} hub. We research the best {vert.lower()} products — "
             f"{sub_list} — and rank them with real customer ratings, thousands of verified reviews, "
             f"and our data-driven <strong>Mavrino Score</strong>. Browse our latest guides below."),
        para("<strong>How we rank:</strong> every pick is scored 0–10 on quality, the weight of verified "
             "reviews behind it, and value for money — so you can buy with confidence, fast."),
    ]
    if hero and hero.get("credit"):
        blocks.append(para(f'<em style="font-size:12px;color:#888">{hero["credit"]}</em>'))
    total = 0
    for sub in subs:
        cslug = slug_by_name.get(sub.lower())
        items = posts_in(cslug)
        if not items:
            continue
        total += len(items)
        blocks.append(head(sub))
        lis = "".join(f'<!-- wp:list-item --><li><a href="{p.get("URL")}">{p.get("title","")}</a></li><!-- /wp:list-item -->' for p in items)
        blocks.append(f'<!-- wp:list -->\n<ul class="wp-block-list">{lis}</ul>\n<!-- /wp:list -->')
        cat_url = f"{SITE}/category/{cslug}/"
        blocks.append(para(f'<a href="{cat_url}">See all {sub.lower()} guides →</a>'))
    content = "\n\n".join(blocks)
    title = f"{vert} Buying Guides & Reviews"
    payload = {"title": title, "content": content}
    if hero:
        payload["featured_image"] = hero["id"]
    if vslug in page_id:
        r = requests.post(f"{B}/sites/{s}/posts/{page_id[vslug]}", headers=h, json=payload, timeout=40)
        pid = page_id[vslug]
    else:
        payload.update({"slug": vslug, "type": "page", "status": "publish"})
        r = requests.post(f"{B}/sites/{s}/posts/new", headers=h, json=payload, timeout=40)
        pid = r.json().get("ID")
    pillar_pages[vert] = (pid, vslug)
    print(f"  {vert:20} pillar {'OK' if r.status_code==200 else 'FAIL'}  ({total} guides linked)  /{vslug}/")

print("\npillars:", {v: p[1] for v, p in pillar_pages.items()})
