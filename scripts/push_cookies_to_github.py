"""
push_cookies_to_github.py
--------------------------
Extracts fresh LeetCode cookies from your browser and pushes them
directly into your GitHub Actions secrets — no DevTools, no copy-paste.

Optionally re-triggers the daily workflow after updating secrets.

Usage:
    # Just refresh secrets
    python scripts/push_cookies_to_github.py

    # Refresh secrets AND re-run the daily workflow immediately
    python scripts/push_cookies_to_github.py --trigger

    # Override repo/token from CLI
    python scripts/push_cookies_to_github.py --repo nipunraj96/Cheat-Bot --trigger

Requirements in .env:
    GITHUB_PAT=your_personal_access_token   # needs repo + secrets scope
    GITHUB_REPO=owner/repo-name

How to get a GitHub PAT:
    github.com/settings/tokens → Generate new token (classic)
    → Select scopes: repo (full), secrets (covered under repo)
    → Copy and paste into .env as GITHUB_PAT=

Setup local weekly cron (macOS/Linux) to keep secrets always fresh:
    crontab -e
    # Add this line (adjust path):
    0 9 * * 0 cd /path/to/Cheat-Bot && source .venv/bin/activate && python scripts/push_cookies_to_github.py
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

# -----------------------------------------------------------------------
# Cookie extraction (same logic as extract_cookies.py)
# -----------------------------------------------------------------------

LEETCODE_DOMAIN = "leetcode.com"

BROWSER_EXTRACTORS = [
    ("Arc",      "arc"),
    ("Chrome",   "chrome"),
    ("Brave",    "brave"),
    ("Firefox",  "firefox"),
    ("Edge",     "edge"),
    ("Opera",    "opera"),
    ("Chromium", "chromium"),
]


def _extract_cookies_from_browser() -> tuple[str, str]:
    """
    Try each browser in priority order.
    Returns (session, csrftoken) or raises RuntimeError.
    """
    try:
        import rookiepy  # type: ignore
    except ImportError:
        raise RuntimeError("rookiepy not installed. Run: pip install rookiepy")

    for browser_name, attr in BROWSER_EXTRACTORS:
        extractor = getattr(rookiepy, attr, None)
        if extractor is None:
            continue
        try:
            cookies = extractor([LEETCODE_DOMAIN]) or []
        except Exception:
            continue

        cookie_map = {c["name"]: c["value"] for c in cookies}
        session = cookie_map.get("LEETCODE_SESSION")
        csrftoken = cookie_map.get("csrftoken")

        if session and csrftoken:
            print(f"  found cookies in {browser_name} ✓")
            return session, csrftoken

    raise RuntimeError(
        "Could not find LEETCODE_SESSION + csrftoken in any browser.\n"
        "Make sure you're logged into leetcode.com in Chrome, Arc, or Firefox."
    )


# -----------------------------------------------------------------------
# GitHub Secrets API
# -----------------------------------------------------------------------

def _github_headers(pat: str) -> dict:
    return {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_repo_public_key(client: httpx.Client, repo: str, pat: str) -> dict:
    """Fetch the repo's public key for secret encryption."""
    resp = client.get(
        f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
        headers=_github_headers(pat),
    )
    if resp.status_code == 401:
        raise RuntimeError("GitHub PAT is invalid or expired. Check GITHUB_PAT in .env")
    if resp.status_code == 404:
        raise RuntimeError(f"Repo '{repo}' not found or PAT lacks repo access")
    resp.raise_for_status()
    return resp.json()


def _encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    """
    Encrypt a secret value using the repo's public key (libsodium sealed box).
    GitHub requires this specific encryption before uploading secrets.
    """
    try:
        from nacl import encoding, public  # type: ignore
    except ImportError:
        raise RuntimeError("PyNaCl not installed. Run: pip install PyNaCl")

    pk = public.PublicKey(public_key_b64.encode(), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pk)
    encrypted = sealed_box.encrypt(secret_value.encode())
    return base64.b64encode(encrypted).decode()


def _push_secret(
    client: httpx.Client,
    repo: str,
    pat: str,
    key_id: str,
    public_key_b64: str,
    secret_name: str,
    secret_value: str,
) -> None:
    encrypted = _encrypt_secret(public_key_b64, secret_value)
    resp = client.put(
        f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}",
        headers=_github_headers(pat),
        json={"encrypted_value": encrypted, "key_id": key_id},
    )
    resp.raise_for_status()


# -----------------------------------------------------------------------
# Workflow dispatch (re-trigger)
# -----------------------------------------------------------------------

def _trigger_workflow(
    client: httpx.Client,
    repo: str,
    pat: str,
    workflow_file: str = "daily.yml",
    ref: str = "main",
) -> None:
    """
    Trigger a workflow_dispatch event on the given workflow.
    Waits 3 seconds after secret push to give GitHub time to propagate.
    """
    print("  waiting 3s for secrets to propagate…")
    time.sleep(3)

    resp = client.post(
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches",
        headers=_github_headers(pat),
        json={"ref": ref},
    )
    if resp.status_code == 204:
        return  # success (no content)
    if resp.status_code == 422:
        raise RuntimeError(
            f"Could not trigger workflow — make sure '{ref}' branch exists "
            f"and the workflow has 'workflow_dispatch:' enabled."
        )
    resp.raise_for_status()


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------

def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Push fresh LeetCode cookies to GitHub Actions secrets"
    )
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPO"), help="owner/repo")
    parser.add_argument("--pat", default=os.getenv("GITHUB_PAT"), help="GitHub Personal Access Token")
    parser.add_argument(
        "--from-env",
        action="store_true",
        help="Read cookies from .env instead of extracting from browser",
    )
    parser.add_argument(
        "--trigger",
        action="store_true",
        help="Re-trigger the daily workflow after updating secrets",
    )
    parser.add_argument(
        "--workflow",
        default="daily.yml",
        help="Workflow file to trigger (default: daily.yml)",
    )
    parser.add_argument(
        "--ref",
        default="main",
        help="Branch/tag to run the workflow on (default: main)",
    )
    args = parser.parse_args()

    if not args.repo:
        print("✗ GITHUB_REPO not set. Add it to .env or pass --repo owner/repo-name")
        sys.exit(1)
    if not args.pat:
        print("✗ GITHUB_PAT not set. Add it to .env or pass --pat <token>")
        print("  Get one at: github.com/settings/tokens (needs repo + secrets scope)")
        sys.exit(1)

    if args.from_env:
        session = os.getenv("LEETCODE_SESSION", "").strip()
        csrftoken = os.getenv("LEETCODE_CSRFTOKEN", "").strip()
        if not session or not csrftoken:
            print("✗ LEETCODE_SESSION or LEETCODE_CSRFTOKEN missing from .env")
            sys.exit(1)
        print("  read cookies from .env ✓")
    else:
        print("→ Extracting cookies from browser…")
        try:
            session, csrftoken = _extract_cookies_from_browser()
        except RuntimeError as e:
            print(f"✗ {e}")
            sys.exit(1)

    print(f"→ Connecting to GitHub ({args.repo})…")
    with httpx.Client(timeout=15.0) as client:
        try:
            key_data = _get_repo_public_key(client, args.repo, args.pat)
        except RuntimeError as e:
            print(f"✗ {e}")
            sys.exit(1)

        key_id = key_data["key_id"]
        public_key = key_data["key"]

        print("→ Pushing secrets…")
        for name, value in [
            ("LEETCODE_SESSION", session),
            ("LEETCODE_CSRFTOKEN", csrftoken),
        ]:
            try:
                _push_secret(client, args.repo, args.pat, key_id, public_key, name, value)
                print(f"  {name} ✓")
            except RuntimeError as e:
                print(f"✗ Failed to push {name}: {e}")
                sys.exit(1)

        if args.trigger:
            print(f"→ Triggering {args.workflow} on {args.ref}…")
            try:
                _trigger_workflow(client, args.repo, args.pat, args.workflow, args.ref)
                print(f"  workflow dispatched ✓")
                print(f"  check: https://github.com/{args.repo}/actions")
            except RuntimeError as e:
                print(f"✗ {e}")
                sys.exit(1)

    print("\n✓ Done.")
    if not args.trigger:
        print("  Secrets updated. Run with --trigger to also re-run the workflow.")


if __name__ == "__main__":
    main()
