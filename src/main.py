"""
main.py
-------
Entrypoint — orchestrates the full flow with:
  - Multi-account support
  - Fallback solution cascade
  - Persistent JSONL run log
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

from .leetcode_api import DailyChallenge, fetch_daily_challenge, iter_solutions
from .submit_api import submit, user_status, wait_for_verdict

# -----------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------

LOG_PATH = Path("logs/runs.jsonl")


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise EnvironmentError(
            f"Missing required env var: {name}\n"
            f"  → Run `python scripts/extract_cookies.py` to auto-fill from your browser,\n"
            f"    or paste manually into .env"
        )
    return value


def _load_accounts() -> list[tuple[str, str, str]]:
    """
    Returns list of (account_label, session, csrftoken) tuples.

    Multi-account mode:  set ACCOUNTS=nipun,alt in .env
                         then NIPUN_LEETCODE_SESSION= etc.
    Single-account mode: just set LEETCODE_SESSION= and LEETCODE_CSRFTOKEN=
    """
    accounts_str = os.getenv("ACCOUNTS", "").strip()
    if accounts_str:
        accounts = []
        for raw in accounts_str.split(","):
            label = raw.strip().upper()
            session = _require_env(f"{label}_LEETCODE_SESSION")
            csrftoken = _require_env(f"{label}_LEETCODE_CSRFTOKEN")
            accounts.append((label.lower(), session, csrftoken))
        return accounts
    else:
        return [("default", _require_env("LEETCODE_SESSION"), _require_env("LEETCODE_CSRFTOKEN"))]


def _append_log(entry: dict) -> None:
    """Append one JSON line to logs/runs.jsonl — never overwrites, never fails the bot."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        print(f"  [log] warning: could not write log entry — {exc}")


# -----------------------------------------------------------------------
# Per-account logic
# -----------------------------------------------------------------------

async def _run_account(
    client: httpx.AsyncClient,
    label: str,
    session: str,
    csrftoken: str,
    daily: DailyChallenge,
    max_attempts: int,
    preferred_lang: str | None = None,
) -> str:  # returns: 'ok' | 'auth_failed' | 'failed'
    tag = f"[{label}]" if label != "default" else ""

    print(f"\n{tag} → Checking auth…")
    status = await user_status(client, session, csrftoken)
    if not status["is_signed_in"]:
        print(
            f"{tag} ✗ Not signed in — cookies are stale.\n"
            f"  Fix locally : python scripts/extract_cookies.py\n"
            f"  Fix + re-run: python scripts/push_cookies_to_github.py --trigger"
        )
        _append_log({"ts": datetime.now(timezone.utc).isoformat(), "account": label,
                     "date": daily.date, "problem": daily.title, "slug": daily.slug,
                     "status": "AuthFailed"})
        return "auth_failed"
    print(f"{tag}   logged in as {status['username']} ✓")

    print(f"{tag} → Trying solutions (up to {max_attempts} attempts)…")

    attempt = 0
    final_status = "Failed"
    log_entry: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "account": label,
        "date": daily.date,
        "problem": daily.title,
        "slug": daily.slug,
    }

    async for candidate in iter_solutions(client, daily.slug, preferred_lang=preferred_lang):
        attempt += 1
        print(
            f"{tag}   attempt {attempt}: \"{candidate.source['title']}\" "
            f"· {candidate.lang} · {candidate.source['votes']} votes"
        )

        try:
            result = await submit(
                client, session, csrftoken,
                daily.slug, daily.question_id, candidate.code, candidate.lang,
            )
            print(f"{tag}   submission_id={result['submission_id']}")

            print(f"{tag}   waiting for verdict…")
            verdict = await wait_for_verdict(client, session, csrftoken, result["submission_id"])

            if verdict["state"] != "SUCCESS":
                print(f"{tag}   unexpected verdict state, skipping")
                continue

            status_msg = verdict.get("status_msg", "Unknown")
            correct = verdict.get("total_correct")
            total = verdict.get("total_testcases")
            print(f"{tag}   {status_msg} ({correct}/{total})")

            log_entry.update({
                "lang": candidate.lang,
                "votes": candidate.source["votes"],
                "attempt": attempt,
                "status": status_msg,
                "correct": correct,
                "total": total,
            })

            if status_msg == "Accepted":
                final_status = "Accepted"
                print(f"{tag} ✓ Streak maintained.")
                break

            if attempt >= max_attempts:
                print(f"{tag} ✗ Gave up after {attempt} attempts.")
                final_status = f"Failed ({status_msg})"
                break

            print(f"{tag}   not accepted — trying next solution…")

        except Exception as exc:
            print(f"{tag}   submit error: {exc}")
            if attempt >= max_attempts:
                break
            continue

    else:
        if attempt == 0:
            print(f"{tag} ✗ No valid solutions found for {daily.slug}")
            log_entry["status"] = "NoSolution"

    if "status" not in log_entry:
        log_entry["status"] = final_status

    _append_log(log_entry)
    return "ok" if final_status == "Accepted" else "failed"


# -----------------------------------------------------------------------
# Entrypoint
# -----------------------------------------------------------------------

async def main() -> None:
    load_dotenv()

    max_attempts = int(os.getenv("MAX_FALLBACK_ATTEMPTS", "3"))
    preferred_lang = os.getenv("PREFERRED_LANG", "").strip().lower() or None
    if preferred_lang:
        print(f"  preferred language: {preferred_lang}")
    accounts = _load_accounts()

    async with httpx.AsyncClient(timeout=30.0) as client:

        print("→ Fetching today's Daily Challenge…")
        daily = await fetch_daily_challenge(client)
        print(f"  {daily.date} · {daily.title} · {daily.url}")

        # Run sequentially per account — one submit at a time is cleaner
        statuses = []
        for label, session, csrftoken in accounts:
            result = await _run_account(
                client, label, session, csrftoken, daily, max_attempts, preferred_lang
            )
            statuses.append(result)

    if len(accounts) > 1:
        print(f"\nDone. Processed {len(accounts)} accounts.")

    # Exit code 2 = auth failure (stale cookies) — GitHub Actions detects this
    # and the error message tells user to run push_cookies_to_github.py --trigger
    if all(s == "auth_failed" for s in statuses):
        sys.exit(2)
    if any(s == "failed" for s in statuses):
        sys.exit(1)


def run() -> None:
    """Entry point registered in pyproject.toml."""
    try:
        asyncio.run(main())
    except Exception as err:
        print(f"\n✗ {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run()
