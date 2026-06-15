"""
search_requests.py — turn zero-result site searches into guides (demand-driven).
─────────────────────────────────────────────────────────────────────────────────
Polls the WordPress plugin's secured endpoint for searches that returned nothing,
gates each one with the model, and:
  • accept       → adds a clean keyword to the content queue (drip publishes it)
  • new_category → records it as an expansion signal (real category we don't cover)
  • reject       → junk / not a product → ignored

This makes visitor demand directly drive content, while the same quality gating
keeps junk out. Safe no-op until the plugin is installed (endpoint just 404s).
"""

import os, sys, json, hashlib, requests
from pathlib import Path
from dotenv import load_dotenv

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent / "pipeline"))

import cache_builder as cb
import anthropic

SITE   = os.getenv("SITE_DOMAIN", "https://mavrino.com").rstrip("/")
SECRET = os.getenv("MAVRINO_SEARCH_SECRET", "mvr_sk_a7f3e91c8b2d4f60")
BASE   = f"{SITE}/wp-json/mavrino/v1"
QUEUE  = Path("config/keyword_queue.json")
DONE   = Path("config/keywords_done.json")
DEMAND = Path("config/search_demand.json")
NICHES = sorted(cb.SEED_PRODUCTS.keys())
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def fetch_pending() -> list:
    try:
        r = requests.get(f"{BASE}/search-requests", params={"secret": SECRET}, timeout=20)
        return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []
    except Exception:
        return []   # plugin not installed / endpoint down → no-op


def resolve(query: str):
    try:
        requests.post(f"{BASE}/search-requests/resolve", params={"secret": SECRET},
                      json={"query": query}, timeout=20)
    except Exception:
        pass


def gate(query: str) -> dict:
    prompt = (
        f'A visitor searched our product-review site for: "{query}".\n\n'
        f'We cover these product categories: {", ".join(NICHES)}.\n\n'
        'Classify the search:\n'
        '- "accept": it maps to one of our covered categories and we could write a useful buying '
        'guide. Provide a clean SEO keyword (e.g. "best <category> for <use case>") and the matching niche.\n'
        '- "new_category": a genuine product category we do NOT cover yet (useful expansion signal).\n'
        '- "reject": junk, a typo with no clear product, navigational, or not a product.\n\n'
        'Return JSON only: {"verdict":"accept|new_category|reject","keyword":"...","niche":"..."}'
    )
    try:
        m = client.messages.create(
            model="claude-haiku-4-5", max_tokens=200,
            system="You classify site searches for an affiliate review site. Return valid JSON only.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = m.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        d = json.loads(raw)
        if d.get("verdict") == "accept" and d.get("niche", "").lower() not in [n.lower() for n in NICHES]:
            d["verdict"] = "new_category"   # don't accept a niche we can't actually source
        return d
    except Exception as e:
        print(f"  [search] gate failed for '{query}': {e}")
        return {"verdict": "reject"}


def add_to_queue(keyword: str, niche: str) -> bool:
    queue = json.loads(QUEUE.read_text()) if QUEUE.exists() else []
    done  = set(json.loads(DONE.read_text())) if DONE.exists() else set()
    slug  = hashlib.md5(keyword.lower().strip().encode()).hexdigest()[:8]
    if slug in done or any(k.get("slug") == slug for k in queue):
        return False
    queue.insert(0, {"keyword": keyword, "post_type": "roundup", "slug": slug,
                     "score": 950, "source": "search_demand", "niche": niche})
    QUEUE.write_text(json.dumps(queue, indent=2))
    return True


def record_demand(query: str):
    data = json.loads(DEMAND.read_text()) if DEMAND.exists() else {}
    data[query.lower()] = int(data.get(query.lower(), 0)) + 1
    DEMAND.write_text(json.dumps(data, indent=2))


def ingest() -> dict:
    pending = fetch_pending()
    stats = {"queued": 0, "new_category": 0, "rejected": 0}
    for req in pending:
        q = (req.get("query") or "").strip()
        if not q:
            continue
        g = gate(q)
        verdict = g.get("verdict")
        if verdict == "accept" and g.get("keyword") and g.get("niche"):
            if add_to_queue(g["keyword"], g["niche"]):
                stats["queued"] += 1
                print(f"  [search] queued '{g['keyword']}'  (searched: {q})")
        elif verdict == "new_category":
            record_demand(q)
            stats["new_category"] += 1
        else:
            stats["rejected"] += 1
        resolve(q)
    if any(stats.values()):
        print(f"  [search] demand ingest: {stats}")
    return stats


if __name__ == "__main__":
    ingest()
