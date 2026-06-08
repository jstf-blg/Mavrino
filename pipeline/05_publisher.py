"""
pipeline/05_publisher.py
─────────────────────────
Commits all new HTML files to GitHub in a single daily batch push.
One commit = one Cloudflare build = stays within free tier limits.

Uses GitPython library or falls back to subprocess git commands.
"""

import os, json, subprocess, sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR  = Path(os.getenv("OUTPUT_DIR", "output"))
LOG_FILE    = Path("logs/publish_log.json")
LOG_FILE.parent.mkdir(exist_ok=True)


def run_git(args: list[str], cwd: str = ".") -> tuple[int, str, str]:
    """Run a git command, return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def count_new_files(repo_path: str = ".") -> int:
    """Count untracked/modified HTML files ready to commit."""
    code, out, _ = run_git(["status", "--porcelain"], cwd=repo_path)
    if code != 0:
        return 0
    new_files = [l for l in out.splitlines() if ".html" in l]
    return len(new_files)


def publish_batch(repo_path: str = ".", dry_run: bool = False) -> dict:
    """
    Add all new HTML files and push in a single commit.
    Returns summary dict.
    """
    today    = datetime.utcnow().strftime("%Y-%m-%d")
    new_count = count_new_files(repo_path)

    if new_count == 0:
        print("[publish] Nothing to publish.")
        return {"date": today, "files": 0, "status": "nothing_to_do"}

    print(f"[publish] {new_count} new files to publish for {today}")

    if dry_run:
        print("[publish] DRY RUN — skipping git commands")
        return {"date": today, "files": new_count, "status": "dry_run"}

    # Stage all output files
    code, out, err = run_git(["add", str(OUTPUT_DIR) + "/"], cwd=repo_path)
    if code != 0:
        print(f"[publish] git add failed: {err}")
        return {"date": today, "files": 0, "status": "error", "error": err}

    # Single commit for the whole day's batch
    commit_msg = f"Auto-publish {new_count} posts — {today}"
    code, out, err = run_git(["commit", "-m", commit_msg], cwd=repo_path)
    if code != 0 and "nothing to commit" not in err:
        print(f"[publish] git commit failed: {err}")
        return {"date": today, "files": 0, "status": "error", "error": err}

    # Push to origin main
    code, out, err = run_git(["push", "origin", "main"], cwd=repo_path)
    if code != 0:
        print(f"[publish] git push failed: {err}")
        return {"date": today, "files": new_count, "status": "push_failed", "error": err}

    print(f"[publish] ✓ Pushed {new_count} files → Cloudflare will deploy within 60s")

    result = {"date": today, "files": new_count, "status": "published"}

    # Log it
    log = []
    if LOG_FILE.exists():
        try:
            log = json.loads(LOG_FILE.read_text())
        except Exception:
            log = []
    log.append(result)
    LOG_FILE.write_text(json.dumps(log[-90:], indent=2))  # keep 90 days

    return result


def update_index_and_push(index_html: str, repo_path: str = "."):
    """Update the index page and push it."""
    index_path = OUTPUT_DIR / "index.html"
    index_path.write_text(index_html, encoding="utf-8")

    run_git(["add", str(index_path)], cwd=repo_path)
    run_git(["commit", "--amend", "--no-edit"], cwd=repo_path)


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    result = publish_batch(dry_run=dry)
    print(json.dumps(result, indent=2))
