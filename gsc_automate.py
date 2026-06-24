"""gsc_automate.py — automate Google Search Console (monitor + submit, NOT force-index).

  - submits the sitemap to Search Console
  - inspects each sitemap URL's index coverage (URL Inspection API, ~2000/day quota)
  - writes a status summary to logs/gsc_status.json

There is NO API that "requests indexing" — this submits the sitemap and reports which
URLs Google has indexed vs. is still holding, so we can track progress and catch problems.

Auth: config/gsc_token.json (from gsc_auth.py) locally, OR env GSC_REFRESH_TOKEN +
GSC_CLIENT_ID + GSC_CLIENT_SECRET when run in CI.
Set GSC_SITE_URL if your property is a Domain property (sc-domain:mavrino.com).
"""
import os
import re
import sys
import json
from datetime import datetime, timezone

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES        = ["https://www.googleapis.com/auth/webmasters"]
SITE          = os.getenv("GSC_SITE_URL", "https://mavrino.com/")
SITEMAP       = "https://mavrino.com/sitemap.xml"
TOKEN_FILE    = os.path.join("config", "gsc_token.json")
LOG           = os.path.join("logs", "gsc_status.json")
INSPECT_LIMIT = int(os.getenv("GSC_INSPECT_LIMIT", "40"))   # stay well under the daily quota


def load_creds():
    cid, csec, rt = os.getenv("GSC_CLIENT_ID"), os.getenv("GSC_CLIENT_SECRET"), os.getenv("GSC_REFRESH_TOKEN")
    if rt and cid and csec:
        return Credentials(None, refresh_token=rt, client_id=cid, client_secret=csec,
                           token_uri="https://oauth2.googleapis.com/token", scopes=SCOPES)
    if os.path.exists(TOKEN_FILE):
        return Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    sys.exit("No GSC credentials. Run gsc_auth.py, or set GSC_REFRESH_TOKEN/CLIENT_ID/CLIENT_SECRET.")


def sitemap_urls():
    urls = []
    idx = requests.get(SITEMAP, timeout=20).text
    for child in re.findall(r"<loc>(.*?)</loc>", idx):
        if "image-sitemap" in child:
            continue
        page = requests.get(child, timeout=20).text
        urls += re.findall(r"<loc>(.*?)</loc>", page)
    return list(dict.fromkeys(urls))


def main():
    svc = build("searchconsole", "v1", credentials=load_creds(), cache_discovery=False)

    # 1. (Re)submit the sitemap
    try:
        svc.sitemaps().submit(siteUrl=SITE, feedpath=SITEMAP).execute()
        print("[gsc] sitemap submitted")
    except Exception as e:
        print("[gsc] sitemap submit error:", str(e)[:160])

    # 2. Index coverage on a sample of URLs (oldest sitemap URLs first)
    urls = sitemap_urls()[:INSPECT_LIMIT]
    summary, details = {}, []
    for u in urls:
        try:
            res = svc.urlInspection().index().inspect(body={"inspectionUrl": u, "siteUrl": SITE}).execute()
            cov = res.get("inspectionResult", {}).get("indexStatusResult", {}).get("coverageState", "unknown")
        except Exception as e:
            cov = "error:" + str(e)[:40]
        summary[cov] = summary.get(cov, 0) + 1
        details.append({"url": u, "coverage": cov})

    print(f"[gsc] inspected {len(urls)} URLs -> {summary}")
    os.makedirs("logs", exist_ok=True)
    with open(LOG, "w", encoding="utf-8") as f:
        json.dump({"checked_at": datetime.now(timezone.utc).isoformat(),
                   "site": SITE, "summary": summary, "details": details}, f, indent=2)
    print("[gsc] wrote", LOG)


if __name__ == "__main__":
    main()
