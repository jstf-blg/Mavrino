"""
pipeline/publisher.py
"""
import os, json, subprocess, sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
LOG_FILE = Path("logs/publish_log.json")
LOG_FILE.parent.mkdir(exist_ok=True)


def run_git(args, cwd="."):
    result = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def count_new_files(repo_path="."):
    code, out, _ = run_git(["status", "--porcelain"], cwd=repo_path)
    if code != 0:
        return 0
    return len([l for l in out.splitlines() if ".html" in l])


def publish_batch(repo_path=".", dry_run=False):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    new_count = count_new_files(repo_path)
    if new_count == 0:
        print("[publish] Nothing to publish.")
        return {"date": today, "files": 0, "status": "nothing_to_do"}
    if dry_run:
        return {"date": today, "files": new_count, "status": "dry_run"}
    run_git(["add", str(OUTPUT_DIR) + "/"], cwd=repo_path)
    commit_msg = f"Auto-publish {new_count} posts — {today}"
    run_git(["commit", "-m", commit_msg], cwd=repo_path)
    run_git(["push", "origin", "main"], cwd=repo_path)
    print(f"[publish] Pushed {new_count} files")
    return {"date": today, "files": new_count, "status": "published"}


if __name__ == "__main__":
    print(publish_batch())