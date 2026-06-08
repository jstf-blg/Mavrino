"""
pipeline/04_renderer.py
────────────────────────
Takes generated content JSON + product data and renders final HTML files.

- Picks the right template based on post_type
- Injects real product data (prices, images, affiliate links)
- Adds all schema markup, metadata, and trust signals
- Outputs ready-to-deploy HTML files
"""

import os, json, re, time
from pathlib import Path
from datetime import datetime
from jinja2 import Environment, FileSystemLoader, select_autoescape
from dotenv import load_dotenv

load_dotenv()

TEMPLATES_DIR = Path("templates")
OUTPUT_DIR    = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_DIR.mkdir(exist_ok=True)

# Site config from .env
SITE = {
    "name":        os.getenv("SITE_NAME", "Your Site Name"),
    "domain":      os.getenv("SITE_DOMAIN", "https://yourdomain.com"),
    "author_name": os.getenv("AUTHOR_NAME", "Editorial Team"),
    "author_bio":  os.getenv("AUTHOR_BIO", "Consumer product researcher."),
}

# Template selection per post type
TEMPLATE_MAP = {
    "roundup":       "roundup.html",
    "comparison":    "comparison.html",
    "review":        "roundup.html",   # review uses roundup template (different data structure)
    "budget_roundup":"roundup.html",
    "guide":         "roundup.html",
}

# Jinja2 env
env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)

# Custom Jinja filters
env.filters["truncate"] = lambda s, n=80: (s[:n] + "…") if len(s) > n else s


def slugify(text: str) -> str:
    """Convert keyword to URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text


def render_post(
    content: dict,
    products: list[dict],
    keyword_data: dict,
) -> tuple[str, str]:
    """
    Render a post to HTML.
    Returns (slug, html_content)
    """
    post_type = content.get("post_type", "roundup")
    keyword   = content.get("keyword", "")
    slug      = slugify(keyword)

    # Build products lookup by ASIN
    products_by_asin = {p["asin"]: p for p in products if p.get("asin")}

    # Date helpers
    now               = datetime.utcnow()
    generated_date    = now.strftime("%Y-%m-%d")
    generated_date_display = now.strftime("%B %d, %Y")
    year              = now.year

    # For comparison posts, identify the two product ASINs
    product_a_asin = keyword_data.get("asin_a", "")
    product_b_asin = keyword_data.get("asin_b", "")
    if not product_a_asin and len(products) >= 1:
        product_a_asin = products[0].get("asin", "")
    if not product_b_asin and len(products) >= 2:
        product_b_asin = products[1].get("asin", "")

    # Pick template
    template_name = TEMPLATE_MAP.get(post_type, "roundup.html")
    template      = env.get_template(template_name)

    html = template.render(
        content              = content,
        products             = products,
        products_by_asin     = products_by_asin,
        keyword_data         = keyword_data,
        product_a_asin       = product_a_asin,
        product_b_asin       = product_b_asin,
        site                 = SITE,
        slug                 = slug,
        generated_date       = generated_date,
        generated_date_display = generated_date_display,
        year                 = year,
    )

    return slug, html


def save_post(slug: str, html: str) -> Path:
    """Save rendered HTML to output directory."""
    # Organise into subdirectories to avoid hitting file limits too fast
    # e.g. output/a/ab1c2d3e.html — first char of slug as subdir
    subdir = OUTPUT_DIR / slug[0] if slug else OUTPUT_DIR
    subdir.mkdir(parents=True, exist_ok=True)

    filepath = subdir / f"{slug}.html"
    filepath.write_text(html, encoding="utf-8")
    return filepath


def render_and_save(
    content: dict,
    products: list[dict],
    keyword_data: dict,
) -> Path | None:
    """Full pipeline: render + save. Returns path to saved file."""
    try:
        slug, html = render_post(content, products, keyword_data)
        path = save_post(slug, html)
        size_kb = len(html) / 1024
        print(f"  [render] {slug}.html ({size_kb:.1f} KB)")
        return path
    except Exception as e:
        print(f"  [render] Error: {e}")
        import traceback; traceback.print_exc()
        return None


def render_index(published_posts: list[dict]) -> str:
    """Render a simple index page listing all posts."""
    rows = ""
    for post in sorted(published_posts, key=lambda x: x.get("date", ""), reverse=True)[:200]:
        slug  = post.get("slug", "")
        title = post.get("title", slug)
        date  = post.get("date", "")
        subdir = slug[0] if slug else ""
        url   = f"/{subdir}/{slug}.html"
        rows += f'<li><a href="{url}">{title}</a> <small style="color:#999">{date}</small></li>\n'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{SITE['name']} — Product Reviews &amp; Comparisons</title>
<meta name="description" content="Honest product reviews and comparisons to help you find the best value.">
<style>
body{{font-family:sans-serif;max-width:800px;margin:0 auto;padding:2rem;background:#faf8f4;color:#1a1814}}
h1{{font-size:1.8rem;margin-bottom:.5rem}}
p{{color:#666;margin-bottom:1.5rem}}
ul{{list-style:none;padding:0}}
li{{padding:.5rem 0;border-bottom:1px solid #e4e0d8;font-size:.95rem}}
a{{color:#d4500a}}
footer{{margin-top:3rem;font-size:.78rem;color:#999;border-top:1px solid #e4e0d8;padding-top:1rem}}
</style>
</head>
<body>
<h1>{SITE['name']}</h1>
<p>Honest product reviews and comparisons for US shoppers.</p>
<p style="font-size:.8rem;background:#fffbf0;border:1px solid #e8d48a;padding:.6rem .8rem;border-radius:5px">
  <strong>Disclosure:</strong> We earn commissions from qualifying purchases. This does not affect our recommendations.
</p>
<ul>
{rows}
</ul>
<footer>© {datetime.utcnow().year} {SITE['name']} · <a href="/privacy.html">Privacy</a> · <a href="/disclosure.html">Disclosure</a></footer>
</body>
</html>"""
    return html


def render_static_pages():
    """Create required static pages: privacy, disclosure, about."""
    pages = {
        "privacy.html": _privacy_page(),
        "disclosure.html": _disclosure_page(),
        "about.html": _about_page(),
    }
    for filename, html in pages.items():
        path = OUTPUT_DIR / filename
        path.write_text(html, encoding="utf-8")
        print(f"  [static] {filename}")


def _privacy_page() -> str:
    year = datetime.utcnow().year
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Privacy Policy — {SITE['name']}</title>
<style>body{{font-family:sans-serif;max-width:700px;margin:2rem auto;padding:0 1.5rem;line-height:1.7;color:#333}}h1{{font-size:1.5rem}}h2{{font-size:1.1rem;margin:1.5rem 0 .5rem}}</style>
</head><body>
<h1>Privacy Policy</h1>
<p>Last updated: {datetime.utcnow().strftime('%B %Y')}</p>
<h2>Information we collect</h2>
<p>{SITE['name']} does not directly collect personal information. We use standard web analytics to understand aggregate traffic patterns.</p>
<h2>Cookies</h2>
<p>We use cookies for analytics purposes. Amazon may set cookies when you click affiliate links.</p>
<h2>Amazon Associates</h2>
<p>As an Amazon Associate, we earn from qualifying purchases. Amazon's privacy policy applies to purchases made through our links.</p>
<h2>Contact</h2>
<p>Questions about this policy? Contact us through our About page.</p>
<p><a href="/">← Back to home</a></p>
</body></html>"""


def _disclosure_page() -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Affiliate Disclosure — {SITE['name']}</title>
<style>body{{font-family:sans-serif;max-width:700px;margin:2rem auto;padding:0 1.5rem;line-height:1.7;color:#333}}h1{{font-size:1.5rem}}</style>
</head><body>
<h1>Affiliate Disclosure</h1>
<p>{SITE['name']} participates in the Amazon Services LLC Associates Program, an affiliate advertising program designed to provide a means for sites to earn advertising fees by advertising and linking to Amazon.com.</p>
<p>When you click a product link on this site and make a purchase, we may earn a commission at no additional cost to you. This commission helps fund the research and testing that goes into our recommendations.</p>
<p>Our editorial opinions are our own and are not influenced by affiliate relationships. We only recommend products we believe provide genuine value.</p>
<p><a href="/">← Back to home</a></p>
</body></html>"""


def _about_page() -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>About — {SITE['name']}</title>
<style>body{{font-family:sans-serif;max-width:700px;margin:2rem auto;padding:0 1.5rem;line-height:1.7;color:#333}}h1{{font-size:1.5rem}}</style>
</head><body>
<h1>About {SITE['name']}</h1>
<p>{SITE['name']} helps US shoppers find the best products through honest, data-backed reviews and comparisons.</p>
<p>Our recommendations are based on product specifications, Amazon customer reviews, and pricing data. We clearly disclose our affiliate relationships on every page.</p>
<h2>Our reviewer</h2>
<p><strong>{SITE['author_name']}</strong> — {SITE['author_bio']}</p>
<p><a href="/">← Back to home</a></p>
</body></html>"""


if __name__ == "__main__":
    # Test render with dummy data
    dummy_content = {
        "title": "Best Air Fryers 2026: Top 3 Picks Tested",
        "meta_description": "We tested the top air fryers of 2026. Here are our top picks.",
        "intro": "Looking for an air fryer that actually delivers crispy results?\n\nWe analysed thousands of Amazon reviews and real pricing data to find the best options right now.",
        "winner_asin": "B08975S94R",
        "winner_verdict": "The Ninja Pro XL wins for most households thanks to its 6.5 qt capacity and exceptional crispiness scores in customer reviews.",
        "products": [
            {
                "asin": "B08975S94R",
                "heading": "Best Overall",
                "verdict": "With 4.7 stars across 12,000+ reviews, this Ninja earns its top spot. Reviewers consistently praise the quick preheat and easy-clean basket.",
                "who_its_for": "Most households cooking for 2–4 people",
                "main_pro": "Heats fast, cooks evenly",
                "main_con": "Louder than budget models",
                "quote": "Makes the crispiest fries I've ever had at home. Changed how I cook.",
            }
        ],
        "buying_guide": "Capacity is the first thing to get right.\n\nFor 1–2 people, a 3–4 qt basket is plenty. For families, go 6 qt or above.\n\nNoise matters more than most reviews mention. If you have an open-plan kitchen, check the decibel ratings.",
        "faq": [
            {"q": "How long do air fryers last?", "a": "Most quality models last 3–5 years with regular use. Ninja and Cosori both have strong warranty records."},
            {"q": "Do air fryers use a lot of electricity?", "a": "A typical 1,500W air fryer uses about the same electricity as a microwave. For 20-minute cooking sessions it costs pennies."},
        ],
        "keyword": "best air fryers 2026",
        "post_type": "roundup",
        "generated_at": "2026-01-01T00:00:00Z",
    }

    dummy_products = [{
        "asin": "B08975S94R",
        "title": "Ninja Air Fryer Pro XL 6.5 Qt",
        "price": 129.99,
        "rating": 4.7,
        "review_count": 12450,
        "affiliate_url": "https://www.amazon.com/dp/B08975S94R?tag=test-20",
    }]

    render_static_pages()
    path = render_and_save(dummy_content, dummy_products, {"keyword": "best air fryers 2026"})
    if path:
        print(f"\nTest render saved: {path}")
