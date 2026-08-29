"""
smoke.py
--------
Dry-run script — fetches today's daily challenge and prints
the top-voted solution without touching your account.

Usage:
    python scripts/smoke.py
"""

import asyncio
import sys
import os

# Make sure src/ is importable when running from the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
from dotenv import load_dotenv

from src.leetcode_api import fetch_daily_challenge, pick_best_solution


async def main() -> None:
    load_dotenv()
    async with httpx.AsyncClient(timeout=30.0) as client:
        daily = await fetch_daily_challenge(client)
        print(f"Daily: {daily.title} · {daily.slug} · {daily.url}")

        best = await pick_best_solution(client, daily.slug)
        print(
            f'Picked: "{best.source["title"]}" '
            f"({best.source['votes']} votes, {best.lang}, "
            f"topicId={best.source['topic_id']})"
        )
        print("--- CODE ---")
        print(best.code)


if __name__ == "__main__":
    asyncio.run(main())
