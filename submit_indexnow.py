"""submit_indexnow.py — submit every site URL to IndexNow (Bing / Yandex / DuckDuckGo).

Reads the live sitemap, collects all page URLs, and POSTs them to IndexNow so those
engines crawl them within minutes. Requires INDEXNOW_KEY env AND the matching /{key}.txt
file served by the Mavrino plugin (re-upload the plugin with MAVRINO_INDEXNOW_KEY first).

NOTE: Google does NOT participate in IndexNow — Google still needs Search Console.

    python submit_indexnow.py
"""
import os
import re
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

HOST = "mavrino.com"
KEY  = os.getenv("INDEXNOW_KEY", "").strip()


def collect_urls():
    urls = []
    idx = requests.get(f"https://{HOST}/sitemap.xml", timeout=20).text
    for child in re.findall(r"<loc>(.*?)</loc>", idx):
        if "image-sitemap" in child:
            continue
        page = requests.get(child, timeout=20).text
        urls += re.findall(r"<loc>(.*?)</loc>", page)
    return list(dict.fromkeys(urls))


def main():
    if not KEY:
        print("INDEXNOW_KEY not set — aborting."); sys.exit(1)
    # The key file must be live (plugin re-uploaded) or IndexNow rejects everything.
    kf = requests.get(f"https://{HOST}/{KEY}.txt", timeout=15)
    if kf.status_code != 200 or kf.text.strip() != KEY:
        print(f"Key file https://{HOST}/{KEY}.txt is not serving the key (HTTP {kf.status_code}).")
        print("Re-upload the Mavrino plugin (it serves the key) before running this.")
        sys.exit(1)

    urls = collect_urls()
    print(f"collected {len(urls)} URLs from the sitemap")
    for i in range(0, len(urls), 10000):              # IndexNow accepts up to 10k per request
        batch = urls[i:i + 10000]
        r = requests.post("https://api.indexnow.org/indexnow",
                          json={"host": HOST, "key": KEY,
                                "keyLocation": f"https://{HOST}/{KEY}.txt",
                                "urlList": batch}, timeout=30)
        print(f"  submitted {len(batch)} URLs -> HTTP {r.status_code} "
              f"({'accepted' if r.status_code in (200, 202) else r.text[:80]})")


if __name__ == "__main__":
    main()
