"""
pipeline/taxonomy_manager.py
──────────────────────────────
Fully automated taxonomy, SEO, tagging, schema markup,
internal linking and nightly health monitoring for Mavrino.

Zero manual input required. Ever.

Called in two ways:
  1. During post creation: classify_post(content, products, keyword)
     Returns full taxonomy + SEO data for the post
  2. Nightly health check: run_health_monitor()
     Fixes categories, merges thin ones, splits fat ones

All WordPress operations use the wp_publisher token.
"""

import os, json, time, requests
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
import anthropic

load_dotenv()

client   = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
WP_SITE  = os.getenv("WP_SITE", "mavrino.com")
# Public-facing domain for canonical/schema URLs. WP_SITE may be a numeric blog ID
# (used only for API addressing), so never build human/SEO URLs from it.
SITE_URL = os.getenv("SITE_DOMAIN", "https://mavrino.com").rstrip("/")
API_BASE = "https://public-api.wordpress.com/rest/v1.1"

TAXONOMY_FILE  = Path("config/taxonomy.json")
TAXONOMY_FILE.parent.mkdir(exist_ok=True)

# ══════════════════════════════════════════════════════════════════════════════
# MASTER CATEGORY HIERARCHY
# Parent → children mapping. Claude always picks from this list.
# New categories are added automatically when 5+ posts suggest them.
# ══════════════════════════════════════════════════════════════════════════════

CATEGORY_HIERARCHY = {
    "Kitchen": [
        "Air Fryers", "Blenders", "Coffee Makers", "Espresso Machines",
        "Food Processors", "Stand Mixers", "Toaster Ovens", "Electric Kettles",
        "Juicers", "Rice Cookers", "Instant Pots", "Microwave Ovens",
        "Sous Vide", "Ice Makers", "Bread Makers",
    ],
    "Home": [
        "Robot Vacuums", "Stick Vacuums", "Air Purifiers", "Humidifiers",
        "Dehumidifiers", "Space Heaters", "Fans & Cooling", "Smart Home",
        "Mattresses", "Pillows", "Bedding", "Sofas & Couches", "Storage",
    ],
    "Home Office": [
        "Standing Desks", "Office Chairs", "Monitors", "Mechanical Keyboards",
        "Webcams", "Keyboards & Mice", "Desk Lamps", "Monitor Arms", "Laptop Stands",
    ],
    "Travel": [
        "Luggage", "Travel Backpacks", "Packing Cubes", "Portable Chargers",
        "Travel Pillows", "Carry-On Bags", "Backpacks", "Travel Accessories",
    ],
    "Fitness & Wellness": [
        "Massage Guns", "Fitness Trackers", "Smart Scales", "Yoga Mats",
        "Adjustable Dumbbells", "Treadmills", "Exercise Bikes", "Resistance Bands",
    ],
    "Furniture": [
        "Sofas", "Beds & Bed Frames", "Dining Tables", "Bookshelves",
        "TV Stands", "Coffee Tables", "Wardrobes",
    ],
    "Outdoors": [
        "Coolers", "Tents", "Camping Chairs", "Portable Grills", "Sleeping Bags",
        "BBQ & Grills", "Camping Gear", "Garden Tools", "Outdoor Furniture",
    ],
}

# Price tier tag thresholds in USD
PRICE_TIERS = [
    (0,    50,  "under-$50"),
    (50,   100, "under-$100"),
    (100,  150, "under-$150"),
    (150,  200, "under-$200"),
    (200,  300, "under-$300"),
    (300,  500, "$300-$500"),
    (500,  999999, "premium"),
]

# Category health thresholds
SPLIT_THRESHOLD = 50   # split category when over this many posts
MERGE_THRESHOLD = 3    # merge category when under this many posts


# ══════════════════════════════════════════════════════════════════════════════
# TAXONOMY STATE — persisted to config/taxonomy.json
# ══════════════════════════════════════════════════════════════════════════════

def _default_taxonomy() -> dict:
    return {
        "wp_category_ids":  {},   # name → WordPress category ID
        "wp_tag_ids":       {},   # name → WordPress tag ID
        "post_index":       [],   # list of {slug, title, category, tags, wp_post_id}
        "category_counts":  {},   # category name → post count
        "pending_splits":   [],   # categories flagged for splitting
        "last_health_check": None,
    }

def load_taxonomy() -> dict:
    """Load taxonomy state, always returning a dict with every expected key.

    Merges whatever is on disk onto the default structure so that an empty
    ({}), partial, or corrupt taxonomy.json can't cause KeyErrors downstream.
    """
    data = _default_taxonomy()
    if TAXONOMY_FILE.exists():
        try:
            loaded = json.loads(TAXONOMY_FILE.read_text())
            if isinstance(loaded, dict):
                data.update(loaded)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [taxonomy] Could not read {TAXONOMY_FILE} ({e}) — using defaults")
    return data

def save_taxonomy(data: dict):
    TAXONOMY_FILE.write_text(json.dumps(data, indent=2))


# ══════════════════════════════════════════════════════════════════════════════
# WORDPRESS API HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _get_token() -> str | None:
    from wp_publisher import get_access_token
    return get_access_token()

def _site(token: str = None) -> str:
    """Resolve WP_SITE to a numeric blog ID (the domain can 403 the API)."""
    from wp_publisher import resolve_site
    return resolve_site(token)

def _wp_post(endpoint: str, data: dict, token: str) -> dict | None:
    url     = f"{API_BASE}/sites/{_site(token)}/{endpoint}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.post(url, json=data, headers=headers, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [taxonomy] POST error {endpoint}: {e}")
        return None

def _wp_get(endpoint: str, token: str, params: dict = None) -> dict | None:
    url     = f"{API_BASE}/sites/{_site(token)}/{endpoint}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(url, params=params or {}, headers=headers, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [taxonomy] GET error {endpoint}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# CATEGORY & TAG MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def get_or_create_category(name: str, parent_name: str, token: str, tax: dict) -> int | None:
    """Get or create a WordPress category, with correct parent assignment."""
    # Check local cache first
    cache_key = f"{parent_name}/{name}" if parent_name else name
    if cache_key in tax["wp_category_ids"]:
        return tax["wp_category_ids"][cache_key]

    # Get/create parent first
    parent_id = 0
    if parent_name:
        if parent_name in tax["wp_category_ids"]:
            parent_id = tax["wp_category_ids"][parent_name]
        else:
            # Search for an existing parent before creating (avoids a 400
            # "term exists" when the local cache has been reset)
            existing = _wp_get("categories", token, {"search": parent_name, "number": 10})
            if existing:
                for cat in existing.get("categories", []):
                    if cat["name"].lower() == parent_name.lower():
                        parent_id = cat["ID"]
                        break
            # Create parent only if it doesn't already exist
            if not parent_id:
                result = _wp_post("categories/new", {
                    "name":        parent_name,
                    "description": f"Reviews and comparisons of {parent_name.lower()} products",
                }, token)
                if result and result.get("ID"):
                    parent_id = result["ID"]
                    print(f"  [taxonomy] Created parent category: {parent_name} (ID: {parent_id})")
            if parent_id:
                tax["wp_category_ids"][parent_name] = parent_id

    # Search for existing child category
    result = _wp_get("categories", token, {"search": name, "number": 10})
    if result:
        for cat in result.get("categories", []):
            if cat["name"].lower() == name.lower():
                cat_id = cat["ID"]
                # Self-heal: if the category exists but sits under the wrong parent
                # (e.g. created top-level by an earlier version), re-parent it now so
                # the hierarchy stays consistent as content grows.
                if parent_id and cat_id != parent_id and cat.get("parent", 0) != parent_id:
                    if _wp_post(f"categories/slug:{cat.get('slug','')}", {"parent": parent_id}, token):
                        print(f"  [taxonomy] Normalised '{name}' under '{parent_name}'")
                tax["wp_category_ids"][cache_key] = cat_id
                save_taxonomy(tax)
                return cat_id

    # Create child category
    create_data = {
        "name":        name,
        "description": f"The best {name.lower()} — tested and reviewed",
    }
    if parent_id:
        create_data["parent"] = parent_id

    result = _wp_post("categories/new", create_data, token)
    if result and result.get("ID"):
        cat_id = result["ID"]
        tax["wp_category_ids"][cache_key] = cat_id
        save_taxonomy(tax)
        print(f"  [taxonomy] Created category: {parent_name} → {name} (ID: {cat_id})")
        return cat_id

    return None


def get_or_create_tag(name: str, token: str, tax: dict) -> int | None:
    """Get or create a WordPress tag."""
    tag_key = name.lower().strip()
    if tag_key in tax["wp_tag_ids"]:
        return tax["wp_tag_ids"][tag_key]

    # Search existing
    result = _wp_get("tags", token, {"search": name, "number": 5})
    if result:
        for tag in result.get("tags", []):
            if tag["name"].lower() == tag_key:
                tax["wp_tag_ids"][tag_key] = tag["ID"]
                return tag["ID"]

    # Create
    result = _wp_post("tags/new", {"name": name}, token)
    if result and result.get("ID"):
        tag_id = result["ID"]
        tax["wp_tag_ids"][tag_key] = tag_id
        save_taxonomy(tax)
        return tag_id

    return None


def get_or_create_all_tags(tag_names: list[str], token: str, tax: dict) -> list[int]:
    """Batch get/create tags, return list of IDs."""
    ids = []
    for name in tag_names:
        tag_id = get_or_create_tag(name, token, tax)
        if tag_id:
            ids.append(tag_id)
    return ids


# ══════════════════════════════════════════════════════════════════════════════
# CLAUDE CLASSIFIER
# ══════════════════════════════════════════════════════════════════════════════

CLASSIFIER_SYSTEM = """You are a taxonomy expert for Mavrino, a US product review site.
Your job is to classify posts and generate SEO metadata.
Always respond with valid JSON only. No markdown, no preamble."""

def classify_post(content: dict, products: list[dict], keyword: str) -> dict:
    """
    Use Claude to classify a post and generate all taxonomy + SEO data.
    Returns a complete classification dict.
    """
    title     = content.get("title", keyword)
    post_type = content.get("post_type", "roundup")
    intro     = content.get("intro", "")[:300]

    # Build product summary
    product_summary = []
    for p in products[:3]:
        product_summary.append(
            f"{p.get('title','?')} — ${p.get('price',0):.0f} — {p.get('rating',0)}★"
        )

    # Build hierarchy string for the prompt
    hierarchy_str = ""
    for parent, children in CATEGORY_HIERARCHY.items():
        hierarchy_str += f"\n{parent}: {', '.join(children)}"

    prompt = f"""Classify this product review post and generate all taxonomy and SEO data.

POST:
Title: {title}
Keyword: {keyword}
Type: {post_type}
Intro: {intro}
Products: {chr(10).join(product_summary)}

AVAILABLE CATEGORIES:
{hierarchy_str}

PRICE TIERS: under-$50, under-$100, under-$150, under-$200, under-$300, $300-$500, premium

Return JSON exactly like this:
{{
  "parent_category": "Kitchen",
  "child_category": "Blenders",
  "schema_type": "ItemList",
  "seo_title": "60 chars max, includes keyword naturally",
  "meta_description": "155 chars max, includes keyword, mentions benefit",
  "focus_keyword": "exact keyword to rank for",
  "secondary_keywords": ["keyword2", "keyword3"],
  "brand_tags": ["ninja", "vitamix"],
  "price_tier_tags": ["under-$100", "under-$150"],
  "use_case_tags": ["for-beginners", "for-small-kitchens"],
  "feature_tags": ["quiet-motor", "dishwasher-safe"],
  "all_tags": ["ninja", "vitamix", "under-$100", "for-beginners", "quiet-motor"],
  "internal_link_keywords": ["best blenders 2026", "ninja vs vitamix", "blenders under $100"],
  "schema_type": "ItemList"
}}

Rules:
- parent_category and child_category MUST come from the provided category list
- schema_type: ItemList for roundups, Review for single product reviews, Article for guides, FAQPage if post has FAQ
- brand_tags: lowercase brand names found in product titles
- price_tier_tags: based on actual product prices
- use_case_tags: infer from keyword (for-families, for-beginners, quiet, compact, budget, premium)
- feature_tags: specific product features mentioned (dual-basket, hepa-filter, self-cleaning etc)
- internal_link_keywords: 3 related keywords that probably exist as other posts on the site
- seo_title: different from the post title, optimised for click-through
- all_tags: combined deduplicated list of all tag types"""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=800,
            system=CLASSIFIER_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        result = json.loads(raw)
        result["classified_at"] = datetime.utcnow().isoformat()
        return result

    except json.JSONDecodeError as e:
        print(f"  [taxonomy] Classifier JSON error: {e}")
        return _fallback_classification(keyword, products, post_type)
    except Exception as e:
        print(f"  [taxonomy] Classifier error: {e}")
        return _fallback_classification(keyword, products, post_type)


def _fallback_classification(keyword: str, products: list[dict], post_type: str) -> dict:
    """Rule-based fallback if Claude classifier fails."""
    kw = keyword.lower()

    # Determine parent + child
    parent = "Kitchen"
    child  = "Product Reviews"
    for p, children in CATEGORY_HIERARCHY.items():
        for c in children:
            if c.lower().replace(" ", " ") in kw or any(w in kw for w in c.lower().split()):
                parent = p
                child  = c
                break

    # Price tags from products
    prices = [p.get("price", 0) for p in products if p.get("price")]
    price_tags = []
    if prices:
        avg_price = sum(prices) / len(prices)
        for low, high, tag in PRICE_TIERS:
            if low <= avg_price < high:
                price_tags.append(tag)
                break

    # Brand tags
    brands = list(set([
        p.get("brand", "").lower()
        for p in products
        if p.get("brand")
    ]))

    # Schema type
    schema = "ItemList"
    if post_type == "review":     schema = "Review"
    elif post_type == "guide":    schema = "Article"
    elif "faq" in kw:             schema = "FAQPage"

    all_tags = brands + price_tags
    if "beginner" in kw or "easy" in kw:  all_tags.append("for-beginners")
    if "small" in kw or "compact" in kw:  all_tags.append("compact")
    if "family" in kw or "large" in kw:   all_tags.append("for-families")
    if "quiet" in kw or "silent" in kw:   all_tags.append("quiet")
    if "budget" in kw or "cheap" in kw:   all_tags.append("budget")

    return {
        "parent_category":      parent,
        "child_category":       child,
        "schema_type":          schema,
        "seo_title":            f"Best {keyword.title()} in 2026 — Tested & Reviewed",
        "meta_description":     f"Find the best {keyword} for your home. We tested top models and ranked them by performance, value and ease of use.",
        "focus_keyword":        keyword,
        "secondary_keywords":   [f"best {keyword}", f"{keyword} review"],
        "brand_tags":           brands,
        "price_tier_tags":      price_tags,
        "use_case_tags":        [],
        "feature_tags":         [],
        "all_tags":             list(set(all_tags)),
        "internal_link_keywords": [],
        "classified_at":        datetime.utcnow().isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMA MARKUP GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

def generate_schema(content: dict, products: list[dict], classification: dict, post_url: str) -> str:
    """Generate JSON-LD schema markup for a post."""
    schema_type = classification.get("schema_type", "Article")
    title       = content.get("title", "")
    description = classification.get("meta_description", "")
    author      = os.getenv("AUTHOR_NAME", "Mavrino Editorial")
    date        = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")

    schemas = []

    # Base Article schema — on every post
    schemas.append({
        "@context":      "https://schema.org",
        "@type":         "Article",
        "headline":      title,
        "description":   description,
        "author":        {"@type": "Person", "name": author},
        "publisher":     {"@type": "Organization", "name": "Mavrino", "url": SITE_URL},
        "datePublished": date,
        "dateModified":  date,
        "url":           post_url,
    })

    # ItemList schema for roundups
    if schema_type == "ItemList" and products:
        items = []
        for i, p in enumerate(products[:10]):
            items.append({
                "@type":    "ListItem",
                "position": i + 1,
                "name":     p.get("title", ""),
                "url":      p.get("affiliate_url", ""),
            })
        schemas.append({
            "@context": "https://schema.org",
            "@type":    "ItemList",
            "name":     title,
            "itemListElement": items,
        })

    # Review + AggregateRating for single product reviews
    if schema_type == "Review" and products:
        p = products[0]
        schemas.append({
            "@context":   "https://schema.org",
            "@type":      "Review",
            "name":       title,
            "reviewBody": content.get("intro", "")[:500],
            "author":     {"@type": "Person", "name": author},
            "itemReviewed": {
                "@type":       "Product",
                "name":        p.get("title", ""),
                "brand":       {"@type": "Brand", "name": p.get("brand", "")},
                "offers": {
                    "@type":         "Offer",
                    "price":         str(p.get("price", 0)),
                    "priceCurrency": "USD",
                    "availability":  "https://schema.org/InStock",
                    "url":           p.get("affiliate_url", ""),
                },
            },
            "reviewRating": {
                "@type":       "Rating",
                "ratingValue": str(p.get("rating", 4.5)),
                "bestRating":  "5",
                "worstRating": "1",
            },
        })

    # FAQPage schema
    faq_items = content.get("faq", [])
    if faq_items:
        schemas.append({
            "@context":   "https://schema.org",
            "@type":      "FAQPage",
            "mainEntity": [
                {
                    "@type":          "Question",
                    "name":           item.get("q", ""),
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text":  item.get("a", ""),
                    },
                }
                for item in faq_items[:5]
            ],
        })

    # Combine into one script block
    if len(schemas) == 1:
        schema_json = json.dumps(schemas[0], indent=2)
    else:
        schema_json = json.dumps(schemas, indent=2)

    return f'<script type="application/ld+json">\n{schema_json}\n</script>'


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL LINKING
# ══════════════════════════════════════════════════════════════════════════════

def find_internal_links(classification: dict, current_slug: str, tax: dict) -> list[dict]:
    """
    Find relevant published posts to link to from the current post.
    Returns list of {title, url, anchor_text} dicts.
    """
    links       = []
    post_index  = tax.get("post_index", [])
    child_cat   = classification.get("child_category", "")
    current_tags = set(classification.get("all_tags", []))

    if not post_index:
        return []

    scored = []
    for post in post_index:
        if post.get("slug") == current_slug:
            continue  # don't link to self
        if not post.get("wp_url"):
            continue

        score = 0
        # Same child category = strongest signal
        if post.get("child_category") == child_cat:
            score += 10
        # Same parent category
        elif post.get("parent_category") == classification.get("parent_category"):
            score += 4
        # Tag overlap
        post_tags = set(post.get("tags", []))
        overlap   = len(current_tags & post_tags)
        score    += overlap * 2

        if score > 0:
            scored.append((score, post))

    # Sort by score, take top 4
    scored.sort(key=lambda x: x[0], reverse=True)
    for _, post in scored[:4]:
        links.append({
            "title":       post.get("title", ""),
            "url":         post.get("wp_url", ""),
            "anchor_text": post.get("title", "")[:60],
        })

    return links


def inject_internal_links(wp_content: str, links: list[dict]) -> str:
    """Append an internal links section to WordPress post content."""
    if not links:
        return wp_content

    link_items = "\n".join([
        f'<!-- wp:list-item --><li><a href="{l["url"]}">{l["anchor_text"]}</a></li><!-- /wp:list-item -->'
        for l in links
    ])

    related_block = f"""
<!-- wp:group {{"className":"related-posts","style":{{"border":{{"width":"1px","color":"#e4e0d8"}},"spacing":{{"padding":{{"all":"20px"}}}}}}}} -->
<div class="wp-block-group related-posts" style="border-color:#e4e0d8;border-width:1px;padding:20px">
<!-- wp:heading {{"level":3}} -->
<h3>Related Reviews</h3>
<!-- /wp:heading -->
<!-- wp:list -->
<ul class="wp-block-list">
{link_items}
</ul>
<!-- /wp:list -->
</div>
<!-- /wp:group -->"""

    return wp_content + related_block


# ══════════════════════════════════════════════════════════════════════════════
# MAIN CLASSIFY AND PUBLISH HELPER
# Called from wp_publisher for every post
# ══════════════════════════════════════════════════════════════════════════════

def process_post(
    content:    dict,
    products:   list[dict],
    keyword:    str,
    wp_content: str,
    token:      str,
) -> dict:
    """
    Full taxonomy + SEO processing for a post before publishing.
    Returns enriched post_data dict ready for WordPress API.
    """
    tax = load_taxonomy()

    # 1. Classify
    print(f"  [taxonomy] Classifying '{keyword[:40]}'...")
    classification = classify_post(content, products, keyword)
    parent = classification.get("parent_category", "Kitchen")
    child  = classification.get("child_category",  "Product Reviews")
    print(f"  [taxonomy] → {parent} / {child}")

    # 2. Get/create categories
    cat_id = get_or_create_category(child, parent, token, tax)
    cat_ids = [cat_id] if cat_id else []

    # 3. Get/create tags
    all_tag_names = classification.get("all_tags", [])
    tag_ids       = get_or_create_all_tags(all_tag_names, token, tax)

    # 4. Find internal links
    slug  = content.get("keyword", keyword).lower().replace(" ", "-")[:50]
    links = find_internal_links(classification, slug, tax)
    if links:
        wp_content = inject_internal_links(wp_content, links)
        print(f"  [taxonomy] Added {len(links)} internal links")

    # 5. Generate schema markup — stored in meta, NOT injected into content.
    # WordPress.com strips <script> tags from post_content, which left the raw
    # JSON-LD visible as text at the top of the post. Keep the data in postmeta so
    # it can be rendered into <head> later (theme/plugin) without corrupting the body.
    post_url = f"{SITE_URL}/?p=new"  # placeholder, updated after publish
    schema   = generate_schema(content, products, classification, post_url)

    # 6. Update taxonomy state
    tax["category_counts"][child] = tax["category_counts"].get(child, 0) + 1

    # 7. Build Yoast SEO metadata
    seo_metadata = [
        {"key": "_yoast_wpseo_title",         "value": classification.get("seo_title", "")},
        {"key": "_yoast_wpseo_metadesc",       "value": classification.get("meta_description", "")},
        {"key": "_yoast_wpseo_focuskw",        "value": classification.get("focus_keyword", keyword)},
        {"key": "_mavrino_parent_category",    "value": parent},
        {"key": "_mavrino_child_category",     "value": child},
        {"key": "_mavrino_schema_type",        "value": classification.get("schema_type", "Article")},
        {"key": "_mavrino_schema_jsonld",      "value": schema},
        {"key": "_mavrino_classified_at",      "value": classification.get("classified_at", "")},
        {"key": "_mavrino_has_affiliate_links","value": "false"},
    ]

    save_taxonomy(tax)

    return {
        "content":    wp_content,
        "categories": cat_ids,
        "tags":       tag_ids,
        "metadata":   seo_metadata,
        "classification": classification,
    }


def record_published_post(post_log_entry: dict, classification: dict):
    """
    After a post is published, record it in the taxonomy index
    for future internal linking.
    """
    tax = load_taxonomy()
    tax["post_index"].append({
        "slug":            post_log_entry.get("slug", ""),
        "title":           post_log_entry.get("title", ""),
        "wp_post_id":      post_log_entry.get("wp_post_id", 0),
        "wp_url":          post_log_entry.get("wp_url", ""),
        "parent_category": classification.get("parent_category", ""),
        "child_category":  classification.get("child_category", ""),
        "tags":            classification.get("all_tags", []),
        "published_at":    datetime.utcnow().strftime("%Y-%m-%d"),
    })
    # Keep index manageable
    tax["post_index"] = tax["post_index"][-2000:]
    save_taxonomy(tax)


# ══════════════════════════════════════════════════════════════════════════════
# NIGHTLY HEALTH MONITOR
# ══════════════════════════════════════════════════════════════════════════════

def normalise_category_parents(token: str = None, tax: dict = None) -> int:
    """Ensure every child category sits under its correct parent (self-healing).

    Walks CATEGORY_HIERARCHY and re-parents any existing WordPress category that
    drifted (e.g. created top-level by an older code path). Only touches children
    whose parent category already exists — it won't pre-create empty parents.
    Returns the number of categories re-parented. Safe to run repeatedly.
    """
    token = token or _get_token()
    if not token:
        return 0
    if tax is None:
        tax = load_taxonomy()

    res  = _wp_get("categories", token, {"number": 100})
    cats = res.get("categories", []) if res else []
    by_name = {c["name"].lower(): c for c in cats}

    fixed = 0
    for parent_name, children in CATEGORY_HIERARCHY.items():
        parent = by_name.get(parent_name.lower())
        if not parent:
            continue  # parent doesn't exist yet — nothing to nest under
        parent_id = parent["ID"]
        for child_name in children:
            child = by_name.get(child_name.lower())
            if not child or child["ID"] == parent_id:
                continue
            if child.get("parent", 0) != parent_id:
                if _wp_post(f"categories/slug:{child['slug']}", {"parent": parent_id}, token):
                    tax["wp_category_ids"][f"{parent_name}/{child_name}"] = child["ID"]
                    print(f"  [taxonomy] Normalised '{child_name}' under '{parent_name}'")
                    fixed += 1
    if fixed:
        save_taxonomy(tax)
    return fixed


def run_health_monitor():
    """
    Nightly job — checks taxonomy health and fixes issues automatically.
    Run via GitHub Actions on a separate schedule (e.g. 4am UTC).
    """
    print(f"\n{'='*50}")
    print(f"  Taxonomy Health Monitor — {datetime.utcnow().strftime('%Y-%m-%d')}")
    print(f"{'='*50}\n")

    tax   = load_taxonomy()
    token = _get_token()
    if not token:
        print("[health] Auth failed — skipping")
        return

    fixes = 0

    # ── 1. Detect fat categories (need splitting) ──────────────────────────
    print("[health] Checking for oversized categories...")
    for category, count in tax.get("category_counts", {}).items():
        if count >= SPLIT_THRESHOLD:
            print(f"  [health] SPLIT: '{category}' has {count} posts — flagging for split")
            if category not in tax.get("pending_splits", []):
                tax.setdefault("pending_splits", []).append(category)
                _auto_split_category(category, token, tax)
                fixes += 1

    # ── 2. Detect thin categories (need merging) ────────────────────────────
    print("[health] Checking for thin categories...")
    for category, count in list(tax.get("category_counts", {}).items()):
        if 0 < count < MERGE_THRESHOLD:
            print(f"  [health] MERGE: '{category}' has only {count} posts — merging into parent")
            _merge_thin_category(category, token, tax)
            fixes += 1

    # ── 3. Fix uncategorised posts ──────────────────────────────────────────
    print("[health] Checking for uncategorised posts...")
    uncategorised = _get_uncategorised_posts(token)
    for post in uncategorised[:10]:  # fix up to 10 per run
        print(f"  [health] Reclassifying: '{post.get('title','?')[:40]}'")
        _reclassify_post(post, token, tax)
        fixes += 1

    # ── 4. Normalise category hierarchy (re-parent any drifted children) ────
    print("[health] Normalising category hierarchy...")
    fixes += normalise_category_parents(token, tax)

    # ── 5. Update last health check timestamp ──────────────────────────────
    tax["last_health_check"] = datetime.utcnow().isoformat()
    save_taxonomy(tax)

    print(f"\n[health] Complete — {fixes} fixes applied")
    print(f"[health] Post index size: {len(tax.get('post_index', []))}")
    print(f"[health] Categories tracked: {len(tax.get('category_counts', {}))}\n")


def _auto_split_category(category: str, token: str, tax: dict):
    """
    When a category gets too big, use Claude to suggest sub-categories
    and create them in WordPress.
    """
    # Get posts in this category from index
    posts_in_cat = [
        p for p in tax.get("post_index", [])
        if p.get("child_category") == category
    ]
    if not posts_in_cat:
        return

    titles = [p.get("title", "") for p in posts_in_cat[:30]]

    try:
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=400,
            system="You are a taxonomy expert. Respond with JSON only.",
            messages=[{"role": "user", "content": f"""
These {len(titles)} posts are all in the '{category}' category.
Suggest 3-5 sub-categories to split them into.

Post titles sample: {json.dumps(titles[:15])}

Return JSON: {{"sub_categories": ["Sub Cat 1", "Sub Cat 2", "Sub Cat 3"]}}
"""}],
        )
        raw  = message.content[0].text.strip()
        data = json.loads(raw)
        for sub in data.get("sub_categories", []):
            get_or_create_category(sub, category, token, tax)
            print(f"  [health] Created sub-category: {category} → {sub}")
    except Exception as e:
        print(f"  [health] Split error for '{category}': {e}")


def _merge_thin_category(category: str, token: str, tax: dict):
    """
    Find posts in a thin category and move them to the parent.
    """
    # Find parent
    parent = None
    for p, children in CATEGORY_HIERARCHY.items():
        if category in children:
            parent = p
            break

    if not parent:
        return

    # Update post index — reassign to parent
    for post in tax.get("post_index", []):
        if post.get("child_category") == category:
            post["child_category"] = parent
            # Update in WordPress
            if post.get("wp_post_id") and token:
                parent_cat_id = get_or_create_category(parent, "", token, tax)
                if parent_cat_id:
                    _wp_post(f"posts/{post['wp_post_id']}", {
                        "categories": [parent_cat_id]
                    }, token)

    # Remove from counts
    tax["category_counts"].pop(category, None)
    save_taxonomy(tax)
    print(f"  [health] Merged '{category}' into '{parent}'")


def _get_uncategorised_posts(token: str) -> list[dict]:
    """Get posts with no category or only the default Uncategorized."""
    result = _wp_get("posts", token, {
        "category": "uncategorized",
        "number":   20,
        "fields":   "ID,title,content",
    })
    if result:
        return result.get("posts", [])
    return []


def _reclassify_post(post: dict, token: str, tax: dict):
    """Reclassify an uncategorised post using its title."""
    title   = post.get("title", "")
    post_id = post.get("ID")
    if not title or not post_id:
        return

    # Use fallback classifier based on title
    classification = _fallback_classification(title.lower(), [], "roundup")
    parent = classification.get("parent_category", "Kitchen")
    child  = classification.get("child_category", "Product Reviews")

    cat_id = get_or_create_category(child, parent, token, tax)
    if cat_id:
        _wp_post(f"posts/{post_id}", {"categories": [cat_id]}, token)
        print(f"  [health] Reclassified '{title[:40]}' → {parent}/{child}")


# ══════════════════════════════════════════════════════════════════════════════
# STATS
# ══════════════════════════════════════════════════════════════════════════════

def get_taxonomy_stats() -> dict:
    tax = load_taxonomy()
    return {
        "total_posts_indexed":  len(tax.get("post_index", [])),
        "categories_tracked":   len(tax.get("category_counts", {})),
        "wp_categories_cached": len(tax.get("wp_category_ids", {})),
        "wp_tags_cached":       len(tax.get("wp_tag_ids", {})),
        "pending_splits":       tax.get("pending_splits", []),
        "last_health_check":    tax.get("last_health_check"),
        "top_categories": sorted(
            tax.get("category_counts", {}).items(),
            key=lambda x: x[1], reverse=True
        )[:10],
    }


if __name__ == "__main__":
    import sys
    if "--health" in sys.argv:
        run_health_monitor()
    else:
        stats = get_taxonomy_stats()
        print(json.dumps(stats, indent=2))
