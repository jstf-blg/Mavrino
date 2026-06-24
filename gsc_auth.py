"""gsc_auth.py — one-time OAuth consent → a reusable Search Console token.

PREREQS (do these once in Google Cloud Console, project mavrino-1782184546062):
  1. Enable the Search Console API:
     https://console.cloud.google.com/apis/library/searchconsole.googleapis.com?project=mavrino-1782184546062
  2. On the OAuth client, add this EXACT Authorized redirect URI:  http://localhost:8765/
     https://console.cloud.google.com/apis/credentials?project=mavrino-1782184546062
  3. Verify https://mavrino.com in Search Console with the SAME Google account
     (Site Kit "Sign in with Google" does this in a click).

Then run from the repo root:
    python gsc_auth.py "C:\\Users\\steve\\Downloads\\credentials.json"

It opens your browser → approve → it saves config/gsc_token.json (gitignored) and prints
the refresh token (only needed if we later move automation to GitHub Actions).
"""
import sys
import os
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES    = ["https://www.googleapis.com/auth/webmasters"]
TOKEN_OUT = os.path.join("config", "gsc_token.json")


def main():
    cred = sys.argv[1] if len(sys.argv) > 1 else "credentials.json"
    if not os.path.exists(cred):
        sys.exit(f"credentials file not found: {cred}")
    flow  = InstalledAppFlow.from_client_secrets_file(cred, SCOPES)
    creds = flow.run_local_server(port=8765, prompt="consent", access_type="offline",
                                  authorization_prompt_message="Opening your browser for Google consent...")
    os.makedirs("config", exist_ok=True)
    with open(TOKEN_OUT, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    print(f"\nSaved {TOKEN_OUT}")
    rt = json.loads(creds.to_json()).get("refresh_token") or ""
    print("refresh_token captured:", (rt[:10] + "...") if rt else "(none — re-run; consent must grant offline access)")


if __name__ == "__main__":
    main()
