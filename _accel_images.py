"""Accelerator: resolve real images for the curated catalogue ASINs from this
(residential) IP, paced to avoid Amazon throttling. Re-checks blocked/tentative
ones (force=True); skips ones already ok. Checkpoints every 10."""
import sys, time, json
from pathlib import Path
sys.path.insert(0, "pipeline")
import product_images as pi

cat = json.loads(Path("pipeline/seed_catalogue.json").read_text(encoding="utf-8"))
seen, curated = set(), []
for prods in cat.values():
    for p in prods:
        a = p["asin"]
        if a not in seen:
            seen.add(a); curated.append(a)

cache = pi._load()
def is_ok(a): e = cache.get(a, {}); return e.get("status") == "ok" and e.get("image_url")
todo = [a for a in curated if not is_ok(a)]
print(f"{len(curated)} curated ASINs | {len(todo)} still need a real image", flush=True)

GAP = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
new_ok = 0
for i, a in enumerate(todo, 1):
    e = pi.resolve_image(a, cache=cache, force=True)
    st = e.get("status")
    if st == "ok":
        new_ok += 1
    print(f"[{i}/{len(todo)}] {a} -> {st}", flush=True)
    if i % 10 == 0:
        pi._save(cache)
    time.sleep(GAP)
pi._save(cache)

ok_total = sum(1 for a in curated if is_ok(a))
print(f"\nPass done. new ok this pass: {new_ok} | total curated with image: {ok_total}/{len(curated)}", flush=True)
