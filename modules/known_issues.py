"""Pokémon GO "Release Notes & Known Issues" watcher (owner, 2026-11-23).

Scopely's Helpshift section lists its articles by creation (id). Every minute
the watcher reads the list and the newest article: a new id is a new known
issue; a different text in the newest article is an edit, reported as a
diff. A text that matches one of its last versions is a flap (Helpshift
serves two variants of the same article in turn) and is not reported.
"""

import difflib
import hashlib
import html
import re

SECTION_URL = (
    "https://scopelyexplore.helpshift.com/hc/en/5-pokemon-go/section/"
    "116-release-notes-known-issues/"
)
BASE = "https://scopelyexplore.helpshift.com"
KEEP_VERSIONS = 5
_ARTICLE_RE = re.compile(r'href="(/hc/en/5-pokemon-go/faq/(\d+)-[a-z0-9-]+/)"')


def parse_section(page):
    """[(id, url)] of the section's articles, oldest first, no repeats."""
    seen, out = set(), []
    for path, number in _ARTICLE_RE.findall(page or ""):
        if number not in seen:
            seen.add(number)
            out.append((int(number), BASE + path))
    return sorted(out)


def _plain(fragment):
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>", "\n", fragment)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def parse_article(page):
    """{"title", "body", "status"} of an article page, or None."""
    page = page or ""
    title = re.search(r"<title>(.*?)</title>", page, re.S)
    start = page.find('class="faq-body')
    if not title or start < 0:
        return None
    start = page.find(">", start) + 1
    end = page.find("</div>", start)
    body = _plain(page[start:end])
    if not body:
        return None
    name = html.unescape(title.group(1)).split(" — ")[0].strip()
    status = re.search(r"(?im)^Issue status:\s*(.+)$", body)
    return {
        "title": name[:200],
        "body": body[:3000],
        "status": status.group(1).strip() if status else "",
    }


def fingerprint(article):
    return hashlib.sha256(
        (article["title"] + "\n" + article["body"]).encode()
    ).hexdigest()[:16]


def diff(old, new):
    lines = difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
    return "\n".join(list(lines)[2:])[:1500]


def decide(state, ids, newest_id, article):
    """What to report this minute, and the new state.

    state: {"ids": [...], "latest": {"id", "body", "versions": [fp, ...]}}
    Returns (events, state); events are ("new", id) and ("edit", old_body).
    The first run only records, so nothing already there is announced. An
    article text seen in the last KEEP_VERSIONS versions is a flap: no event.
    """
    known = set(state.get("ids") or [])
    events = [] if not known else [("new", i) for i in ids if i not in known]
    latest = dict(state.get("latest") or {})
    if article is not None:
        fp = fingerprint(article)
        if latest.get("id") != newest_id:
            latest = {"id": newest_id, "body": article["body"], "versions": [fp]}
        elif fp not in (latest.get("versions") or []):
            if known and ("new", newest_id) not in events:
                events.append(("edit", latest.get("body", "")))
            latest["body"] = article["body"]
            latest["versions"] = (list(latest.get("versions") or []) + [fp])[
                -KEEP_VERSIONS:
            ]
    return events, {"ids": sorted(known | set(ids)), "latest": latest}
