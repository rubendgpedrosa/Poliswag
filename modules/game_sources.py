"""More game sources for the watcher (owner, 2026-11-23): the Play Store
version, pokemongo.com news, and new commits in holoholo-text (game text) and
PokeMiners' pogo_assets (game assets)."""

import html
import re

PLAY_URL = (
    "https://play.google.com/store/apps/details?id=com.nianticlabs.pokemongo"
    "&hl=en&gl=US"
)
NEWS_URL = "https://pokemongo.com/en/news"
NEWS_BASE = "https://pokemongo.com/en/news/"
# GitHub repositories whose new commits are reported: (repo, state key, title).
REPOS = (
    ("sora10pls/holoholo-text", "holo", "Texto do jogo atualizado (holoholo-text)"),
    ("PokeMiners/pogo_assets", "assets", "Assets do jogo atualizados (pogo_assets)"),
)


def commits_api(repo):
    return f"https://api.github.com/repos/{repo}/commits?per_page=10"


def commit_url(repo, sha):
    return f"https://github.com/{repo}/commit/{sha}"


KEEP_SEEN = 300


def parse_play_version(page):
    """The version the Play Store page offers ("0.437.0"), or None."""
    match = re.search(r'\[\[\["(\d+\.\d+\.\d+)"\]\]', page or "")
    return match.group(1) if match else None


def parse_news(page):
    """News slugs on the listing, newest first, no repeats."""
    out = []
    for slug in re.findall(r'href="/(?:en/)?news/([a-z0-9_-]+)"', page or ""):
        if slug not in out:
            out.append(slug)
    return out


def parse_title(page):
    match = re.search(r"<title>(.*?)</title>", page or "", re.S)
    if not match:
        return None
    return html.unescape(match.group(1)).split(" — ")[0].strip()[:200] or None


def parse_commits(data):
    """[(sha, first line of the message)] newest first, from the GitHub API."""
    out = []
    for item in data if isinstance(data, list) else []:
        sha = item.get("sha") if isinstance(item, dict) else None
        message = ((item.get("commit") or {}).get("message") or "").strip()
        if isinstance(sha, str) and sha:
            out.append((sha, message.splitlines()[0][:200] if message else ""))
    return out


def unseen(seen, current):
    """Items of `current` not in `seen` (oldest first), and the new seen
    list; an empty `seen` is a first run and reports nothing."""
    fresh = [] if not seen else [i for i in current if i not in seen]
    merged = list(dict.fromkeys(list(current) + list(seen or [])))[:KEEP_SEEN]
    return list(reversed(fresh)), merged
