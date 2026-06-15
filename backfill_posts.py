"""
backfill_posts.py — bring already-published Mavrino posts up to current standards
─────────────────────────────────────────────────────────────────────────────────
For every published post:
  1. Close pingbacks (pings_open=false) so internal links stop creating
     self-pingback "comments" (the linked post's title appearing as a comment).
  2. Ensure a featured image + a category-correct hero image (older posts were
     published before media-library hosting and show no image).
  3. Strip misleading per-product stock photos (the alignright product-card
     images) — generic stock misrepresents specific models. Real per-product
     photos return via the pipeline once the PA-API supplies them.

Idempotent: only changes what needs changing. Usage:
    python backfill_posts.py --dry-run   # report what would change
    python backfill_posts.py             # apply
"""

import os, sys, re, json, time
from pathlib import Path
from dotenv import load_dotenv

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent / "pipeline"))

import requests
import wp_publisher as w
import image_fetcher as imf

B = "https://public-api.wordpress.com/rest/v1.1"

# wp:image blocks aligned right are the per-product card photos; alignwide is the hero.
PRODUCT_IMG_RE = re.compile(
    r'<!-- wp:image [^>]*"align":"right"[^>]*-->.*?<!-- /wp:image -->\s*',
    re.DOTALL,
)
HAS_HERO_RE  = re.compile(r'<!-- wp:image [^>]*"align":"wide"', re.DOTALL)
HERO_BLOCK_RE = re.compile(
    r'<!-- wp:image [^>]*"align":"wide"[^>]*-->.*?<!-- /wp:image -->\s*',
    re.DOTALL,
)


def rehero(dry_run: bool = False):
    """Replace every post's hero with a guaranteed-unique image (no duplicates)."""
    token = w.get_access_token()
    site  = w.resolve_site(token)
    h     = {"Authorization": f"Bearer {token}"}
    # Reset the used-heroes registry so every post is assigned a fresh unique image.
    if not dry_run:
        imf.USED_HEROES_FILE.write_text("[]")
    posts = list_published(token, site)
    print(f"\n{'='*56}\n  Re-hero — {len(posts)} posts {'(dry run)' if dry_run else ''}\n{'='*56}\n")
    done = errors = 0
    seen_urls = set()
    for p in posts:
        pid, title = p["ID"], p.get("title", "")
        try:
            full = requests.get(f"{B}/sites/{site}/posts/{pid}", headers=h,
                                params={"context": "edit"}, timeout=20).json()
            raw  = full.get("content", "")
            hero = imf.get_hero_image(title)   # unique + reserved in registry
            if not hero or not hero.get("url"):
                print(f"  [{pid}] {title[:40]:40} no image available"); errors += 1; continue
            if hero["url"] in seen_urls:
                print(f"  [{pid}] {title[:40]:40} WARNING duplicate selected");
            seen_urls.add(hero["url"])
            if dry_run:
                print(f"  [{pid}] {title[:40]:40} would set hero …{hero['url'][-24:]}"); done += 1; continue
            media = w.upload_media_from_url(hero["url"], token, f"{re.sub(r'[^a-z0-9]+','-',title.lower())[:40]}-hero")
            if not media:
                print(f"  [{pid}] {title[:40]:40} upload FAILED"); errors += 1; continue
            new_raw = HERO_BLOCK_RE.sub("", raw, count=1)
            new_raw = w._hero_block(hero, media) + "\n\n" + new_raw
            r = requests.post(f"{B}/sites/{site}/posts/{pid}", headers=h,
                              json={"content": new_raw, "featured_image": media["ID"]}, timeout=30)
            ok = r.status_code == 200
            done += ok; errors += (not ok)
            print(f"  [{pid}] {title[:40]:40} {'OK' if ok else 'FAIL'}  media #{media['ID']}")
            time.sleep(0.4)
        except Exception as e:
            print(f"  [{pid}] ERROR: {e}"); errors += 1
    print(f"\n  re-heroed: {done}   errors: {errors}   unique images: {len(seen_urls)}\n{'='*56}\n")


def list_published(token, site):
    posts, page_handle = [], None
    while True:
        params = {"number": 100, "status": "publish", "fields": "ID,title,featured_image,URL"}
        if page_handle:
            params["page_handle"] = page_handle
        d = requests.get(f"{B}/sites/{site}/posts", headers={"Authorization": f"Bearer {token}"},
                         params=params, timeout=25).json()
        posts.extend(d.get("posts", []))
        page_handle = d.get("meta", {}).get("next_page")
        if not page_handle or not d.get("posts"):
            break
    return posts


def backfill(dry_run: bool = False):
    token = w.get_access_token()
    site  = w.resolve_site(token)
    h     = {"Authorization": f"Bearer {token}"}
    posts = list_published(token, site)
    print(f"\n{'='*56}\n  Backfill — {len(posts)} published posts {'(dry run)' if dry_run else ''}\n{'='*56}\n")

    stats = {"pings_closed": 0, "hero_added": 0, "product_imgs_stripped": 0, "skipped": 0, "errors": 0}

    for p in posts:
        pid, title = p["ID"], p.get("title", "")
        try:
            full = requests.get(f"{B}/sites/{site}/posts/{pid}", headers=h,
                                params={"context": "edit"}, timeout=20).json()
            raw  = full.get("content", "")
            updates, notes = {}, []

            # 1. Close pings (nested 'discussion' form — the top-level field is ignored)
            if full.get("discussion", {}).get("pings_open", False):
                updates["discussion"] = {"pings_open": False}
                notes.append("pings→closed")

            # 2. Strip misleading per-product (alignright) images
            stripped = PRODUCT_IMG_RE.sub("", raw)
            n_removed = len(PRODUCT_IMG_RE.findall(raw))
            if n_removed:
                raw = stripped
                updates["content"] = raw
                notes.append(f"-{n_removed} product img")
                stats["product_imgs_stripped"] += n_removed

            # 3. Ensure a hero + featured image
            if not p.get("featured_image"):
                hero_image = imf.get_hero_image(title)
                if hero_image and hero_image.get("url"):
                    media = None if dry_run else w.upload_media_from_url(
                        hero_image["url"], token, f"{re.sub(r'[^a-z0-9]+','-',title.lower())[:40]}-hero")
                    if dry_run:
                        notes.append("would add hero+featured")
                    elif media:
                        updates["featured_image"] = media["ID"]
                        if not HAS_HERO_RE.search(raw):
                            raw = w._hero_block(hero_image, media) + "\n\n" + raw
                            updates["content"] = raw
                        notes.append("hero+featured added")
                        stats["hero_added"] += 1

            if "discussion" in updates:
                stats["pings_closed"] += 1

            if updates and not dry_run:
                r = requests.post(f"{B}/sites/{site}/posts/{pid}", headers=h, json=updates, timeout=30)
                ok = r.status_code == 200
                if not ok:
                    stats["errors"] += 1
                print(f"  [{pid}] {title[:42]:42} {'OK' if ok else 'FAIL'}: {', '.join(notes) or 'no change'}")
                time.sleep(0.4)
            elif updates and dry_run:
                print(f"  [{pid}] {title[:42]:42} would: {', '.join(notes)}")
            else:
                stats["skipped"] += 1
        except Exception as e:
            stats["errors"] += 1
            print(f"  [{pid}] ERROR: {e}")

    print(f"\n{'-'*56}")
    print(f"  pings closed:           {stats['pings_closed']}")
    print(f"  heroes added:           {stats['hero_added']}")
    print(f"  product imgs stripped:  {stats['product_imgs_stripped']}")
    print(f"  unchanged:              {stats['skipped']}   errors: {stats['errors']}")
    print(f"{'='*56}\n")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    if "--rehero" in sys.argv:
        rehero(dry_run=dry)
    else:
        backfill(dry_run=dry)
