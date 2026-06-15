"""
build_pillars.py — generate/refresh topical "pillar" pages, one per vertical.
─────────────────────────────────────────────────────────────────────────────
Each pillar is a hub page that introduces the vertical, explains the Mavrino
Score, and links down to every guide in its subcategories (pillar -> cluster).
The primary nav points each vertical at its pillar. Re-run anytime to refresh
the links as new guides publish (safe to run repeatedly).
"""

import sys, html, requests
sys.path.insert(0, "pipeline")
from dotenv import load_dotenv; load_dotenv()
import wp_publisher as w

t = w.get_access_token(); s = w.resolve_site(t); h = {"Authorization": f"Bearer {t}"}
B = "https://public-api.wordpress.com/rest/v1.1"
SITE = "https://mavrino.com"

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


pillar_pages = {}
for vert, (vslug, subs) in VERTICALS.items():
    sub_list = ", ".join(subs[:-1]) + " and " + subs[-1]
    blocks = [
        para(f"Welcome to Mavrino's {vert} hub. We research the best {vert.lower()} products — "
             f"{sub_list} — and rank them with real customer ratings, thousands of verified reviews, "
             f"and our data-driven <strong>Mavrino Score</strong>. Browse our latest guides below."),
        para("<strong>How we rank:</strong> every pick is scored 0–10 on quality, the weight of verified "
             "reviews behind it, and value for money — so you can buy with confidence, fast."),
    ]
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
    if vslug in page_id:
        r = requests.post(f"{B}/sites/{s}/posts/{page_id[vslug]}", headers=h, json={"title": title, "content": content}, timeout=40)
        pid = page_id[vslug]
    else:
        r = requests.post(f"{B}/sites/{s}/posts/new", headers=h,
                          json={"title": title, "slug": vslug, "content": content, "type": "page", "status": "publish"}, timeout=40)
        pid = r.json().get("ID")
    pillar_pages[vert] = (pid, vslug)
    print(f"  {vert:20} pillar {'OK' if r.status_code==200 else 'FAIL'}  ({total} guides linked)  /{vslug}/")

print("\npillars:", {v: p[1] for v, p in pillar_pages.items()})
