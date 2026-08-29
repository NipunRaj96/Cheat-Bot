"""
extract_cookies.py
------------------
Pulls your LeetCode session cookies straight out of your browser
and writes them into .env — no DevTools, no copying, no pasting.

Works on macOS, Windows, Linux.
Supports: Arc, Chrome, Brave, Firefox, Edge, Opera, Chromium, Safari (macOS only).

Usage:
    # Single account (writes LEETCODE_SESSION and LEETCODE_CSRFTOKEN)
    python scripts/extract_cookies.py

    # Multi-account (writes NIPUN_LEETCODE_SESSION etc.)
    python scripts/extract_cookies.py --account nipun

Requirements:
    pip install rookiepy

macOS note:
    Chrome encrypts cookies using your Keychain. The first run may prompt
    you for your system password — that's expected and normal.
    If you get a PermissionError, grant Full Disk Access to your terminal:
    System Settings → Privacy & Security → Full Disk Access.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


LEETCODE_DOMAIN = "leetcode.com"
ENV_PATH = Path(".env")

# Browser extractors in priority order.
# Arc and Chrome first since they're most common among devs.
BROWSER_EXTRACTORS = [
    ("Arc",      "arc"),
    ("Chrome",   "chrome"),
    ("Brave",    "brave"),
    ("Firefox",  "firefox"),
    ("Edge",     "edge"),
    ("Opera",    "opera"),
    ("Chromium", "chromium"),
]


def _try_browser(name: str, attr: str) -> list[dict] | None:
    """
    Attempt to extract cookies from a single browser.
    Returns a list of cookie dicts on success, None if the browser
    isn't installed or extraction fails.
    """
    try:
        import rookiepy  # type: ignore
        extractor = getattr(rookiepy, attr, None)
        if extractor is None:
            return None
        cookies = extractor([LEETCODE_DOMAIN])
        return cookies if cookies else None
    except Exception:
        return None


def _find_cookies() -> tuple[str, str] | None:
    """
    Try each browser in order. Return (session, csrftoken) on the first
    browser that has both cookies, or None if nothing works.
    """
    for browser_name, attr in BROWSER_EXTRACTORS:
        cookies = _try_browser(browser_name, attr)
        if not cookies:
            continue

        cookie_map = {c["name"]: c["value"] for c in cookies}
        session = cookie_map.get("LEETCODE_SESSION")
        csrftoken = cookie_map.get("csrftoken")

        if session and csrftoken:
            print(f"  found cookies in {browser_name} ✓")
            return session, csrftoken

        print(f"  {browser_name}: found cookies but missing session/csrftoken — trying next")

    return None


def _update_env(updates: dict[str, str], env_path: Path = ENV_PATH) -> None:
    """
    Update or append key=value pairs in the .env file.
    Preserves all existing lines, only touches the keys in `updates`.
    """
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()

    for key, value in updates.items():
        found = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}=") or line.startswith(f"# {key}="):
                lines[i] = f"{key}={value}"
                found = True
                break
        if not found:
            lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract LeetCode cookies from your browser into .env"
    )
    parser.add_argument(
        "--account",
        metavar="NAME",
        default=None,
        help=(
            "Account label for multi-account mode. "
            "E.g. --account nipun writes NIPUN_LEETCODE_SESSION=..."
        ),
    )
    args = parser.parse_args()

    try:
        import rookiepy  # noqa: F401
    except ImportError:
        print("✗ rookiepy not installed. Run: pip install rookiepy")
        sys.exit(1)

    print(f"→ Scanning browsers for leetcode.com cookies…")
    result = _find_cookies()

    if result is None:
        print(
            "\n✗ Could not find LEETCODE_SESSION + csrftoken in any browser.\n"
            "  Make sure you're logged into LeetCode in Chrome, Arc, or Firefox,\n"
            "  then try again. If Chrome is open, close it and retry.\n"
            "  macOS: grant Full Disk Access to your terminal if you see PermissionError."
        )
        sys.exit(1)

    session, csrftoken = result

    if args.account:
        label = args.account.upper()
        env_keys = {
            f"{label}_LEETCODE_SESSION": session,
            f"{label}_LEETCODE_CSRFTOKEN": csrftoken,
        }
        print(f"  writing {label}_LEETCODE_SESSION and {label}_LEETCODE_CSRFTOKEN to {ENV_PATH}")
    else:
        env_keys = {
            "LEETCODE_SESSION": session,
            "LEETCODE_CSRFTOKEN": csrftoken,
        }
        print(f"  writing LEETCODE_SESSION and LEETCODE_CSRFTOKEN to {ENV_PATH}")

    _update_env(env_keys)

    print(f"\n✓ Done. .env updated. Run `python -m src.main` to submit.")


if __name__ == "__main__":
    main()
