"""schedule_batch.py — keep one post scheduled for every upcoming hour.

Generates unique posts through the deep content pipeline, prioritising THIN categories
(fewest published posts), and schedules each at the next empty hourly slot via WP
future-dating (status=future → WordPress auto-publishes at that time, no runner needed).

Idempotent + resumable: it reads the posts already scheduled in WP and only fills the
gaps, so it's safe to run on a cron every few hours and survives timeouts. The done-list
guarantees no keyword is ever published twice (unique posts only).

Env knobs:
  SCHEDULE_HORIZON_HOURS  how many hours ahead to keep filled (default 30)
  SCHEDULE_PER_RUN_CAP    max posts to generate per invocation (default 8)
"""
import os, sys, time
from datetime import datetime, timezone, timedelta
from collections import Counter
from pathlib import Path

sys.path.insert(0, "pipeline")
from dotenv import load_dotenv; load_dotenv()
import requests
import drip, run_pipeline as rp, content_generator as cg, cache_builder as cb, amazon_data as ad
import post_templates as ptpl
import wp_publisher as wp

HORIZON = int(os.getenv("SCHEDULE_HORIZON_HOURS", "30"))
CAP     = int(os.getenv("SCHEDULE_PER_RUN_CAP", "8"))

t = wp.get_access_token(); s = wp.resolve_site(t)
B = "https://public-api.wordpress.com/rest/v1.1"; HDR = {"Authorization": f"Bearer {t}"}


def _niche(kw):
    kwl = (kw or "").lower(); best, bl = None, 0
    for n in drip.NICHE_TO_VERTICAL:
        for f in (n, n[:-1] if n.endswith("s") else n):
            if f and f in kwl and len(n) > bl:
                best, bl = n, len(n)
    return best


def _filled_hours():
    """UTC on-the-hour slots that already hold a future-scheduled post."""
    out = set()
    r = requests.get(f"{B}/sites/{s}/posts", headers=HDR,
                     params={"status": "future", "number": 100, "fields": "ID,date"}, timeout=25).json()
    for p in r.get("posts", []):
        try:
            d = datetime.fromisoformat(p["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
            out.add(d.replace(minute=0, second=0, microsecond=0))
        except Exception:
            pass
    return out


def _empty_slots():
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    taken = _filled_hours()
    return [now + timedelta(hours=i) for i in range(1, HORIZON + 1)
            if (now + timedelta(hours=i)) not in taken]


def _products(kw, pt, kd):
    asins = rp.get_asins_for_keyword(kd)
    products = ad.get_multiple_products(asins) if asins else []
    if not products:
        products = cb.get_products_for_keyword(kw, count=rp.products_needed(kw, pt))
    return products


def run():
    cb.build_all_caches()
    slots = _empty_slots()
    print(f"\n{'='*56}\n  Schedule batch — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    if not slots:
        print(f"  schedule already full for the next {HORIZON}h\n{'='*56}"); return
    done = rp.load_done(); plog = rp.load_posts_log()

    templates = list(ptpl.TEMPLATES.keys())
    all_niches = sorted(set(drip.NICHE_TO_VERTICAL.keys()))
    # thin-category priority: niches with the fewest published posts come first, so
    # coverage spreads across EVERY category before any niche/template pair repeats.
    nc = Counter(_niche(e.get("keyword", "")) for e in plog if _niche(e.get("keyword", "")))
    done_combos = {(_niche(e.get("keyword", "")), e.get("template"))
                   for e in plog if e.get("template") and _niche(e.get("keyword", ""))}
    cands = [(n, tpl) for tpl in templates for n in all_niches if (n, tpl) not in done_combos]
    cands.sort(key=lambda c: (nc.get(c[0], 0), templates.index(c[1])))

    n_fill = min(len(slots), CAP)
    print(f"  empty slots next {HORIZON}h: {len(slots)} | niche×template combos left: {len(cands)} | filling up to {n_fill}\n{'='*56}")

    made, si, ci = 0, 0, 0
    while made < n_fill and ci < len(cands):
        niche, tmpl = cands[ci]; ci += 1
        slug = f"tpl-{tmpl}-{niche}".replace(" ", "-")
        if slug in done:
            continue
        products = cb.get_products_for_keyword(f"best {niche}", count=8)
        # Quality gate: a roundup needs a real shortlist. Niches with a thin/empty
        # catalogue (e.g. "desk lamps") otherwise ship hollow "1 Cheapest X" posts —
        # skip them entirely until their catalogue is curated.
        if len(products) < 3:
            print(f"  skip [{tmpl}] {niche}: only {len(products)} product(s) in catalogue")
            continue
        built = ptpl.build(tmpl, niche, products)
        if not built:
            continue
        kw, pt, sel = built
        content = cg.generate_content(kw, pt, sel)
        if not content:
            continue
        slot = slots[si]; si += 1
        iso = slot.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        res = wp.publish_to_wordpress(content, sel, {"keyword": kw, "post_type": pt, "slug": slug}, schedule_date=iso)
        if res:
            done.add(slug); done_combos.add((niche, tmpl))
            plog.append({"date": slot.strftime("%Y-%m-%d"), "keyword": kw, "post_type": pt, "slug": slug,
                         "wp_post_id": res.get("wp_post_id"), "title": res.get("title", ""),
                         "wp_url": res.get("wp_url", ""), "scheduled_for": iso, "template": tmpl})
            made += 1
            print(f"  scheduled @ {iso}  [{tmpl}] {niche}")
        rp.save_done(done); rp.save_posts_log(plog)
        time.sleep(1)

    print(f"\n  scheduled this run: {made} | combos remaining: {len(cands) - ci}\n{'='*56}")


if __name__ == "__main__":
    run()
