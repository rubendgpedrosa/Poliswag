"""The picture on an event card, checked before it is posted.

A posted card keeps its thumbnail URL forever, so a dead link at posting time
stays a blank card even after the feed corrects it. On 2026-09-26 the Rio de
Janeiro City Safari went out with ScrapedDuck's placeholder filed under the
event's article folder (404); the same file exists in the shared events
folder, and the feed swapped in the real picture hours later.
"""

import posixpath

import aiohttp

from modules.http_client import get_session

_EVENTS_FOLDER = "https://cdn.leekduck.com/assets/img/events/"
_TIMEOUT = aiohttp.ClientTimeout(total=5)


async def _status(url):
    async with get_session().head(
        url, timeout=_TIMEOUT, allow_redirects=True
    ) as response:
        return response.status


async def usable_image(url, status=_status):
    """`url` if it loads, else the shared copy of a misfiled placeholder, else
    None. A failed check keeps `url`: a hiccup must not strip every picture."""
    if not url:
        return None
    try:
        if await status(url) < 400:
            return url
    except Exception:
        return url
    name = posixpath.basename(url.split("?", 1)[0])
    fallback = _EVENTS_FOLDER + name
    if fallback != url and name.endswith("-default.jpg"):
        try:
            if await status(fallback) < 400:
                return fallback
        except Exception:
            return None
    return None
