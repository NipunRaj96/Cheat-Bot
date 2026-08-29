"""
leetcode_api.py
---------------
Read-only layer — talks to LeetCode's GraphQL API.
Fetches the daily challenge and scrapes the top-voted community solution.
No auth required here; everything is public.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import TypedDict

import httpx

GRAPHQL_URL = "https://leetcode.com/graphql/"

_HEADERS = {
    "Content-Type": "application/json",
    "Referer": "https://leetcode.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
}


async def _gql(client: httpx.AsyncClient, query: str, variables: dict | None = None) -> dict:
    """Fire a GraphQL request and return `data`, raising on errors."""
    payload = {"query": query, "variables": variables or {}}
    resp = await client.post(GRAPHQL_URL, json=payload, headers=_HEADERS)
    resp.raise_for_status()
    body = resp.json()
    if errors := body.get("errors"):
        msgs = "; ".join(e["message"] for e in errors)
        raise RuntimeError(f"GraphQL errors: {msgs}")
    return body["data"]


# ---------------------------------------------------------------------------
# Daily Challenge
# ---------------------------------------------------------------------------

@dataclass
class DailyChallenge:
    date: str
    slug: str
    title: str
    url: str
    question_id: str


async def fetch_daily_challenge(client: httpx.AsyncClient) -> DailyChallenge:
    query = """
    query questionOfToday {
      activeDailyCodingChallengeQuestion {
        date
        link
        question { questionId titleSlug title }
      }
    }
    """
    data = await _gql(client, query)
    d = data["activeDailyCodingChallengeQuestion"]
    return DailyChallenge(
        date=d["date"],
        slug=d["question"]["titleSlug"],
        title=d["question"]["title"],
        question_id=d["question"]["questionId"],
        url=f"https://leetcode.com{d['link']}",
    )


# ---------------------------------------------------------------------------
# Language definitions
# ---------------------------------------------------------------------------

@dataclass
class LangDef:
    tag: str            # tag used in LeetCode's GraphQL queries
    submit_lang: str    # value sent to the submit endpoint
    priority: int       # tiebreaker — lower wins (python3 = 1)
    detect_pattern: re.Pattern   # relaxed: is there any code here?
    required_pattern: re.Pattern # strict: is it a submittable Solution?


LANGUAGES: list[LangDef] = [
    LangDef("python3",    "python3",    1,  re.compile(r"def\s+\w+\s*\(|class\s+Solution"),        re.compile(r"class\s+Solution\s*:")),
    LangDef("java",       "java",       2,  re.compile(r"public\s+(class|int|void|List|String|boolean)"), re.compile(r"class\s+Solution\b")),
    LangDef("cpp",        "cpp",        3,  re.compile(r"vector<|#include|int\s+\w+\s*\("),         re.compile(r"class\s+Solution\s*\{")),
    LangDef("javascript", "javascript", 4,  re.compile(r"var\s+\w+\s*=\s*function|const\s+\w+\s*=\s*\(|function\s+\w+\s*\("), re.compile(r"var\s+\w+\s*=\s*function|=\s*function\s*\(")),
    LangDef("typescript", "typescript", 5,  re.compile(r"function\s+\w+\s*\(|const\s+\w+\s*:\s*|=>\s*"),  re.compile(r"function\s+\w+\s*\(")),
    LangDef("golang",     "golang",     6,  re.compile(r"func\s+\w+\s*\("),                         re.compile(r"func\s+\w+\s*\(")),
    LangDef("csharp",     "csharp",     7,  re.compile(r"public\s+class\s+Solution|using\s+System"), re.compile(r"public\s+class\s+Solution\b")),
    LangDef("ruby",       "ruby",       8,  re.compile(r"def\s+\w+|class\s+Solution"),               re.compile(r"class\s+Solution\b")),
    LangDef("rust",       "rust",       9,  re.compile(r"impl\s+Solution|fn\s+\w+\s*\("),            re.compile(r"impl\s+Solution\b")),
    LangDef("kotlin",     "kotlin",     10, re.compile(r"class\s+Solution|fun\s+\w+\s*\("),          re.compile(r"class\s+Solution\b")),
]

# Aliases used in markdown fenced code block tags → our internal tag
_FENCED_LANG_ALIASES: dict[str, str] = {
    "py": "python3", "python": "python3", "python3": "python3",
    "c++": "cpp", "cpp": "cpp", "cxx": "cpp",
    "java": "java",
    "js": "javascript", "javascript": "javascript",
    "ts": "typescript", "typescript": "typescript",
    "go": "golang", "golang": "golang",
    "cs": "csharp", "csharp": "csharp", "c#": "csharp",
    "rb": "ruby", "ruby": "ruby",
    "rs": "rust", "rust": "rust",
    "kt": "kotlin", "kotlin": "kotlin",
}

_LANG_BY_TAG: dict[str, LangDef] = {l.tag: l for l in LANGUAGES}


# ---------------------------------------------------------------------------
# Community solutions — two endpoints with automatic fallback
# ---------------------------------------------------------------------------

class SolutionPost(TypedDict):
    topic_id: str
    title: str
    votes: int
    language: str  # internal tag


async def _query_top_solutions_legacy(
    client: httpx.AsyncClient,
    question_slug: str,
    language_tag: str | None = None,
) -> list[dict]:
    """Older `questionSolutions` GraphQL. Fast, but returns 0 for recent problems."""
    query = """
    query communitySolutions(
      $questionSlug: String!
      $skip: Int!
      $first: Int!
      $orderBy: TopicSortingOption
      $languageTags: [String!]
    ) {
      questionSolutions(
        filters: {
          questionSlug: $questionSlug
          skip: $skip
          first: $first
          orderBy: $orderBy
          languageTags: $languageTags
        }
      ) { solutions { id title post { voteCount } } }
    }
    """
    variables = {
        "questionSlug": question_slug,
        "skip": 0,
        "first": 10,
        "orderBy": "most_votes",
        "languageTags": [language_tag] if language_tag else [],
    }
    data = await _gql(client, query, variables)
    return [
        {"topic_id": str(s["id"]), "title": s["title"], "votes": s["post"]["voteCount"]}
        for s in data["questionSolutions"]["solutions"]
    ]


async def _query_top_solutions_ugc(
    client: httpx.AsyncClient,
    question_slug: str,
    language_tag: str | None = None,
) -> list[dict]:
    """Newer UGC endpoint — handles brand-new problems where legacy returns nothing."""
    query = """
    query ugcArticleSolutionArticles(
      $questionSlug: String!
      $orderBy: ArticleOrderByEnum
      $skip: Int
      $first: Int
      $tagSlugs: [String!]
    ) {
      ugcArticleSolutionArticles(
        questionSlug: $questionSlug
        orderBy: $orderBy
        skip: $skip
        first: $first
        tagSlugs: $tagSlugs
      ) {
        edges { node { topicId title reactions { count reactionType } } }
      }
    }
    """
    variables = {
        "questionSlug": question_slug,
        "orderBy": "HOT",
        "skip": 0,
        "first": 15,
        "tagSlugs": [language_tag] if language_tag else [],
    }
    data = await _gql(client, query, variables)
    results = []
    for edge in data["ugcArticleSolutionArticles"]["edges"]:
        node = edge["node"]
        upvotes = next(
            (r["count"] for r in node["reactions"] if r["reactionType"] == "UPVOTE"), 0
        )
        results.append({"topic_id": str(node["topicId"]), "title": node["title"], "votes": upvotes})
    return results


async def _query_top_solutions(
    client: httpx.AsyncClient,
    question_slug: str,
    language_tag: str | None = None,
) -> list[dict]:
    """Try legacy endpoint first; fall back to UGC if it returns nothing."""
    try:
        legacy = await _query_top_solutions_legacy(client, question_slug, language_tag)
        if legacy:
            return legacy
    except Exception:
        pass
    try:
        return await _query_top_solutions_ugc(client, question_slug, language_tag)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Solution body + code extraction
# ---------------------------------------------------------------------------

async def fetch_solution_body(client: httpx.AsyncClient, topic_id: str) -> str:
    query = """
    query solutionDetail($topicId: ID!) {
      ugcArticleSolutionArticle(topicId: $topicId) { content title }
    }
    """
    data = await _gql(client, query, {"topicId": topic_id})
    article = data.get("ugcArticleSolutionArticle")
    if not article:
        raise RuntimeError(f"No solution article for topicId={topic_id}")
    return article["content"]


def _decode_article_escapes(s: str) -> str:
    """
    LeetCode's article API sometimes double-encodes escape sequences.
    Undo those so extracted code actually compiles.
    """
    return (
        s.replace(r"\r\n", "\n")
         .replace(r"\n", "\n")
         .replace(r"\t", "\t")
         .replace(r"\'", "'")
         .replace(r'\"', '"')
         .replace(r"\\", "\\")
    )


_FENCE_RE = re.compile(r"```([a-zA-Z0-9+#]*)\s*\n([\s\S]*?)```")


def extract_submittable_blocks(markdown: str) -> list[dict]:
    """
    Pull every fenced code block that looks like a complete LeetCode answer
    (i.e. has `class Solution` / `impl Solution` / etc.).
    Returns list of {"code": str, "lang": str} dicts.
    """
    normalized = _decode_article_escapes(markdown)
    results = []

    for m in _FENCE_RE.finditer(normalized):
        raw_tag = m.group(1).strip().lower()
        body = m.group(2).strip()
        if not body:
            continue

        if raw_tag:
            internal_tag = _FENCED_LANG_ALIASES.get(raw_tag)
            lang_def = _LANG_BY_TAG.get(internal_tag) if internal_tag else None
            if lang_def and lang_def.required_pattern.search(body):
                results.append({"code": body, "lang": lang_def.submit_lang})
        else:
            # Untagged fence — sniff language by patterns
            for lang_def in LANGUAGES:
                if lang_def.detect_pattern.search(body) and lang_def.required_pattern.search(body):
                    results.append({"code": body, "lang": lang_def.submit_lang})
                    break

    return results


# ---------------------------------------------------------------------------
# Main picker
# ---------------------------------------------------------------------------

@dataclass
class BestSolution:
    code: str
    lang: str
    source: SolutionPost


async def iter_solutions(
    client: httpx.AsyncClient,
    slug: str,
    preferred_lang: str | None = None,
) -> AsyncGenerator[BestSolution, None]:
    """
    Async generator that yields BestSolution objects.

    Order:
      1. If preferred_lang is set — solutions in that language first (by votes).
      2. Then all remaining solutions (by votes) as fallback.

    Caller keeps consuming until Accepted or gives up after N attempts.
    """
    # Parallel fetch across all languages
    per_lang = await asyncio.gather(
        *[_query_top_solutions(client, slug, lang.tag) for lang in LANGUAGES],
        return_exceptions=True,
    )

    all_posts: list[SolutionPost] = []
    for lang, results in zip(LANGUAGES, per_lang):
        if isinstance(results, Exception):
            continue
        for post in results:
            all_posts.append(SolutionPost(
                topic_id=post["topic_id"],
                title=post["title"],
                votes=post["votes"],
                language=lang.tag,
            ))

    if not all_posts:
        raise RuntimeError(f"No community solutions found for {slug}")

    # Sort: highest votes first, tiebreak by language priority
    all_posts.sort(key=lambda p: (-p["votes"], _LANG_BY_TAG[p["language"]].priority))

    # If a language preference is set, bubble those solutions to the front.
    # The global best remains reachable as fallback if preferred lang fails.
    if preferred_lang:
        preferred_posts = [p for p in all_posts if p["language"] == preferred_lang]
        other_posts    = [p for p in all_posts if p["language"] != preferred_lang]
        ordered = preferred_posts + other_posts
    else:
        ordered = all_posts

    seen_topic_ids: set[str] = set()
    for post in ordered:
        if post["topic_id"] in seen_topic_ids:
            continue
        seen_topic_ids.add(post["topic_id"])

        try:
            body = await fetch_solution_body(client, post["topic_id"])
        except Exception:
            continue

        blocks = extract_submittable_blocks(body)
        if not blocks:
            continue

        lang_def = _LANG_BY_TAG.get(post["language"])
        preferred = next(
            (b for b in blocks if lang_def and b["lang"] == lang_def.submit_lang), None
        )
        chosen = preferred or blocks[0]
        yield BestSolution(code=chosen["code"], lang=chosen["lang"], source=post)


async def pick_best_solution(client: httpx.AsyncClient, slug: str) -> BestSolution:
    """Convenience wrapper — returns the single highest-ranked valid solution."""
    async for solution in iter_solutions(client, slug):
        return solution
    raise RuntimeError(
        f"Found posts for {slug} but none had a submittable code block"
    )
