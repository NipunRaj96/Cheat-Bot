# Cheat-Bot

I built this because I genuinely could not be bothered to solve a LeetCode daily problem every single day just to keep a number going up. The streak means nothing about your skill — it just means you showed up. So I automated the showing up.

This bot runs every morning at 6am, grabs the most upvoted community solution for that day's problem, and submits it under your account. By the time you wake up, the streak is already maintained. You didn't write a line of solution code. That's the whole point.

It runs headless, uses no browser automation (no Selenium, no Playwright), and talks directly to LeetCode's own GraphQL and submit endpoints — the same ones your browser hits when you manually submit.

---

## What it does

1. Hits LeetCode's GraphQL API and fetches today's Daily Challenge.
2. Queries top-voted community solutions across **10 languages in parallel** — Python, Java, C++, JavaScript, TypeScript, Go, C#, Ruby, Rust, Kotlin. Most votes wins. Ties break toward Python.
3. Parses the solution post's markdown and extracts a **submittable code block** — skips posts that are just explanations with no `class Solution` or equivalent.
4. Submits the code to your account via `POST /problems/{slug}/submit/`.
5. Polls `/submissions/detail/{id}/check/` until LeetCode returns a verdict.
6. If it fails (Wrong Answer, TLE) — tries the next best solution, up to 3 attempts.
7. Logs every run to `logs/runs.jsonl`.

---

## How it works under the hood

### The API layer (`src/leetcode_api.py`)

LeetCode exposes two separate GraphQL endpoints for community solutions:
- **Legacy** (`questionSolutions`) — fast, ordered by votes, but returns 0 results for problems added after a certain point
- **UGC** (`ugcArticleSolutionArticles`) — LeetCode's newer internal endpoint, handles all problems including recent ones

The bot tries the legacy endpoint first; if it returns nothing, it falls back to UGC. This dual-query approach means it works regardless of how old or new the problem is.

Solutions are fetched **per language, in parallel** using `asyncio.gather`. So instead of 10 sequential requests, it fires all 10 at once and collects results. After sorting by vote count, it iterates through candidates and tries to extract a valid code block from each post body.

Code extraction works by parsing markdown fenced code blocks (` ```python ... ``` `) using regex, then validating the extracted code against language-specific patterns (e.g., does it contain `class Solution:` for Python? `class Solution {` for C++?). Posts with only explanations and no actual solution class get skipped entirely.

LeetCode's article API also sometimes double-encodes escape sequences in the markdown body (literal `\n`, `\'`, etc.). The bot undoes these before parsing so extracted code actually compiles.

### The submission layer (`src/submit_api.py`)

Authentication is entirely cookie-based — two cookies: `LEETCODE_SESSION` and `csrftoken`. Both are sent on every request, along with the `x-csrftoken` header (CSRF protection). No OAuth, no login flow.

After submitting, LeetCode doesn't return the verdict immediately — it queues it. The bot polls `/submissions/detail/{id}/check/` with an exponential backoff starting at 800ms, growing by 300ms per attempt, capped at 2500ms, with a 90-second total timeout.

### The orchestrator (`src/main.py`)

Fetches the daily challenge once, then runs the full auth → submit → verdict loop per account (sequential to avoid overlapping submissions). Uses exit codes to communicate failure type:

| Exit code | Meaning |
|-----------|---------|
| `0` | All accounts accepted |
| `1` | Solution failed (WA/TLE after all attempts) |
| `2` | Auth failed — cookies are stale |

GitHub Actions reads exit code 2 and prints a specific recovery message telling you exactly what to run.

### Cookie management (`scripts/`)

**`extract_cookies.py`** — reads your browser's cookie store directly using [rookiepy](https://github.com/thewh1teagle/rookie) (a Rust-backed library that handles Chrome's Keychain encryption on macOS, Firefox's NSS, etc.). Tries browsers in order: Arc → Chrome → Brave → Firefox → Edge. Writes cookies straight to `.env`.

**`push_cookies_to_github.py`** — same extraction, but instead of writing to `.env`, it encrypts the cookies using libsodium (what GitHub requires) and pushes them to your repo's Actions secrets via the GitHub API. With `--trigger`, it also fires a `workflow_dispatch` event to re-run the daily workflow immediately after updating secrets.

---

## Edge cases handled

| Scenario | How it's handled |
|----------|-----------------|
| Problem is brand new, legacy API returns nothing | Falls back to UGC GraphQL endpoint |
| Top solution has no `class Solution` (explanation only) | Skipped — next candidate tried |
| Top solution gets Wrong Answer or TLE | Fallback cascade — tries next best, up to `MAX_FALLBACK_ATTEMPTS` |
| Duplicate posts across language queries | Deduplicated by `topic_id` before iterating |
| LeetCode double-encodes escapes in article markdown | `_decode_article_escapes()` undoes them before parsing |
| Cookies expire on GitHub Actions | Exit code 2 + recovery message → run `push_cookies_to_github.py --trigger` |
| Multiple accounts | Iterates sequentially, logs each account separately |
| Log write fails (permissions, disk full) | Caught silently — never crashes the bot |

---

## Setup

### Requirements

- Python 3.9+
- A LeetCode account, logged into a browser on this machine

### Install

```bash
git clone https://github.com/nipunraj96/Cheat-Bot.git
cd Cheat-Bot
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

### Fill your `.env`

**Option A — automatic (recommended):**
```bash
python scripts/extract_cookies.py
```
Reads cookies from your browser, writes to `.env`. Done.

**Option B — manual:**
1. Open [leetcode.com](https://leetcode.com) in a browser (logged in)
2. DevTools → Application → Cookies → `https://leetcode.com`
3. Copy `LEETCODE_SESSION` and `csrftoken` into `.env`

### Run locally

```bash
python -m src.main
```

---

## GitHub Actions setup

The bot ships with a workflow that runs daily at **6:00 AM IST (00:30 UTC)**.

**1. Push to a private GitHub repo:**
```bash
git remote add origin https://github.com/your-username/Cheat-Bot.git
git push -u origin main
```

**2. Add your LeetCode cookies as GitHub secrets:**

First, grab the values from your `.env`:
```bash
cat .env
```

Then go to your repo → **Settings → Secrets and variables → Actions → New repository secret** and add:
- `LEETCODE_SESSION`
- `LEETCODE_CSRFTOKEN`

**3. That's it.** The workflow runs automatically every morning.

---

## Keeping secrets fresh (zero-touch maintenance)

`LEETCODE_SESSION` lasts ~14 days without any LeetCode activity. If you open LeetCode even once a week, the session resets — but GitHub Actions still holds the old value. To keep it always fresh, set up a local weekly script that pushes new cookies automatically.

**One-time: add GitHub credentials to `.env`:**
```bash
# Get a PAT at: github.com/settings/tokens
# Needs: repo + secrets scope
echo "GITHUB_PAT=your_token" >> .env
echo "GITHUB_REPO=your-username/Cheat-Bot" >> .env
```

**Run the sync script:**
```bash
python scripts/push_cookies_to_github.py
```

**Add to local weekly cron** (runs every Sunday at 9am):
```bash
crontab -e
# Add this line (update the path):
0 9 * * 0 cd /path/to/Cheat-Bot && source .venv/bin/activate && python scripts/push_cookies_to_github.py
```

**If the workflow fails and cookies are already stale — fix and re-run in one command:**
```bash
python scripts/push_cookies_to_github.py --trigger
```
This refreshes the secrets AND fires `workflow_dispatch` to re-run the daily workflow immediately.

---

## `.env` reference

| Variable | Required | What it is |
|----------|----------|------------|
| `LEETCODE_SESSION` | Yes | Your LeetCode session cookie |
| `LEETCODE_CSRFTOKEN` | Yes | Your LeetCode CSRF token |
| `GITHUB_PAT` | Only for `push_cookies_to_github.py` | GitHub Personal Access Token (repo + secrets scope) |
| `GITHUB_REPO` | Only for `push_cookies_to_github.py` | `owner/repo-name` |
| `ACCOUNTS` | No | Comma-separated account labels for multi-account mode |
| `MAX_FALLBACK_ATTEMPTS` | No | How many solutions to try before giving up (default: 3) |

**Multi-account mode:** set `ACCOUNTS=nipun,alt` and add `NIPUN_LEETCODE_SESSION=`, `NIPUN_LEETCODE_CSRFTOKEN=`, `ALT_LEETCODE_SESSION=`, `ALT_LEETCODE_CSRFTOKEN=` to `.env`. Cookie extractor supports `--account nipun` flag.

---

## Key implementations

**Dual GraphQL endpoint with fallback** — LeetCode silently migrated newer problems to a different internal API. The bot queries both and falls back gracefully, so it never returns zero results regardless of how old or new the problem is.

**Async parallel language queries** — instead of querying Python, then Java, then C++ sequentially, all 10 language queries fire simultaneously via `asyncio.gather`. Results are merged, sorted by votes, and deduplicated by `topic_id`.

**Fallback solution cascade** — `iter_solutions()` is an async generator that yields valid solutions ranked by votes. `main.py` consumes it until it gets an Accepted verdict or exhausts `MAX_FALLBACK_ATTEMPTS`. The generator transparently handles fetching solution bodies one at a time only as needed — no wasted API calls.

**Typed exit codes** — exit 0/1/2 lets GitHub Actions differentiate between success, solution failure, and auth failure. The workflow shows a targeted recovery message for each case.

**Self-healing secret refresh** — `push_cookies_to_github.py` extracts cookies from your local browser, encrypts them with libsodium (GitHub's required format), pushes via the Secrets API, and optionally triggers `workflow_dispatch` to immediately re-run the pipeline.

**Zero-overhead run log** — append-only JSONL at `logs/runs.jsonl`. One line per run, ~200 bytes. No database, no dependencies. Log writes are wrapped in try/except so a disk error never kills the bot.

---

## Project structure

```
src/
  main.py                   # entrypoint — orchestrates the full flow
  leetcode_api.py           # GraphQL: daily challenge + community solutions
  submit_api.py             # HTTP: auth check, submit, poll verdict
scripts/
  extract_cookies.py        # browser → .env (local cookie setup)
  push_cookies_to_github.py # browser → GitHub secrets (+ optional re-trigger)
  smoke.py                  # dry-run: fetch + print solution, no submit
.github/workflows/
  daily.yml                 # cron at 6am IST, smart failure messages
logs/
  runs.jsonl                # append-only run history (gitignored)
.env.example                # copy to .env and fill in
requirements.txt
pyproject.toml
```

---

## Dry run

```bash
python scripts/smoke.py
```

Fetches today's problem and prints the top-voted solution without touching your account.
