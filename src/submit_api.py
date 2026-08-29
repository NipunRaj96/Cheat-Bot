"""
submit_api.py
-------------
Auth + submission layer — verifies your session, fires the submit,
and polls until LeetCode hands back a verdict.
"""

from __future__ import annotations

import asyncio
from typing import Literal, TypedDict, Union

import httpx

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


def _clean(value: str) -> str:
    """Strip whitespace/newlines that get pasted in from DevTools."""
    return value.strip().replace("\r", "").replace("\n", "").replace("\t", "").replace(" ", "")


def _cookie_header(session: str, csrftoken: str) -> str:
    return f"LEETCODE_SESSION={_clean(session)}; csrftoken={_clean(csrftoken)}"


def _auth_headers(session: str, csrftoken: str) -> dict:
    return {
        "User-Agent": _UA,
        "Referer": "https://leetcode.com/",
        "Cookie": _cookie_header(session, csrftoken),
        "x-csrftoken": _clean(csrftoken),
    }


# ---------------------------------------------------------------------------
# Auth check
# ---------------------------------------------------------------------------

class UserStatus(TypedDict):
    is_signed_in: bool
    username: str


async def user_status(client: httpx.AsyncClient, session: str, csrftoken: str) -> UserStatus:
    headers = {**_auth_headers(session, csrftoken), "Content-Type": "application/json"}
    resp = await client.post(
        "https://leetcode.com/graphql/",
        json={"query": "query { userStatus { isSignedIn username } }"},
        headers=headers,
    )
    resp.raise_for_status()
    body = resp.json()["data"]["userStatus"]
    return UserStatus(is_signed_in=body["isSignedIn"], username=body["username"])


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------

class SubmitResponse(TypedDict):
    submission_id: int


async def submit(
    client: httpx.AsyncClient,
    session: str,
    csrftoken: str,
    slug: str,
    question_id: str,
    code: str,
    lang: str = "python3",
) -> SubmitResponse:
    url = f"https://leetcode.com/problems/{slug}/submit/"
    headers = {
        **_auth_headers(session, csrftoken),
        "Content-Type": "application/json",
        "Origin": "https://leetcode.com",
        "Referer": f"https://leetcode.com/problems/{slug}/description/?envType=daily-question",
    }
    resp = await client.post(
        url,
        json={"lang": lang, "question_id": question_id, "typed_code": code},
        headers=headers,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("submission_id"):
        raise RuntimeError(f"submit returned no submission_id: {body}")
    return SubmitResponse(submission_id=body["submission_id"])


# ---------------------------------------------------------------------------
# Verdict polling
# ---------------------------------------------------------------------------

class CheckPending(TypedDict):
    state: Literal["PENDING", "STARTED"]


class CheckSuccess(TypedDict):
    state: Literal["SUCCESS"]
    status_msg: str
    status_code: int
    run_success: bool
    total_correct: int | None
    total_testcases: int | None
    compile_error: str | None
    runtime_error: str | None
    last_testcase: str | None
    expected_output: str | None
    code_output: str | None


CheckResponse = Union[CheckPending, CheckSuccess]


async def check(
    client: httpx.AsyncClient,
    session: str,
    csrftoken: str,
    submission_id: int,
) -> CheckResponse:
    url = f"https://leetcode.com/submissions/detail/{submission_id}/check/"
    resp = await client.get(url, headers=_auth_headers(session, csrftoken))
    resp.raise_for_status()
    return resp.json()


async def wait_for_verdict(
    client: httpx.AsyncClient,
    session: str,
    csrftoken: str,
    submission_id: int,
    timeout_ms: int = 90_000,
) -> CheckResponse:
    """
    Poll until LeetCode returns state=SUCCESS or we hit the timeout.
    Backoff starts at 800ms and grows by 300ms per attempt, capped at 2500ms.
    """
    elapsed = 0
    delay = 0.8  # seconds

    while elapsed < timeout_ms / 1000:
        result = await check(client, session, csrftoken, submission_id)
        if result["state"] == "SUCCESS":
            return result
        await asyncio.sleep(delay)
        elapsed += delay
        delay = min(delay + 0.3, 2.5)

    raise TimeoutError(f"Verdict timeout after {timeout_ms}ms")
