"""Tells MY_ID when pogoleiria.pt or one of its apps stops answering.

pm2 restarts an app that crashes, but nothing noticed one that stayed up and
answered with errors: on 2026-09-25 a Pokédex release broke Comunidade's SQL
and it served 500s until the next deploy, visible only in the pm2 log.

Each check fetches one page that exercises the app for real (the Pokédex's
Comunidade reads the database), straight from the host's ports so a failure
names the app, plus the public address through Cloudflare. A check is down
after DOWN_AFTER failures in a row, so a deploy's restart doesn't page
anyone. The decision is pure and in-memory: a bot restart mid-outage just
alerts again.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import aiohttp

from modules.config import Config
from modules.http_client import get_session

# One tick is a minute: three in a row outlasts a pm2 restart.
DOWN_AFTER = 3
# How long before a still-down app is worth mentioning again.
REPEAT_AFTER = timedelta(hours=6)
TIMEOUT_SECONDS = 15
# Cloudflare answers 403 to Python's default User-Agent.
USER_AGENT = "Poliswag-health/1.0"


def default_checks(host):
    return (
        ("pogoleiria.pt", "https://pogoleiria.pt/"),
        ("Hub (landing)", f"http://{host}:1080/"),
        ("Quests", f"http://{host}:3001/embed/quests"),
        ("Pokédex", f"http://{host}:3003/embed/pokedex/procurar"),
    )


@dataclass
class CheckState:
    failures: int = 0
    last_error: str | None = None
    alerted_at: datetime | None = None


@dataclass
class SiteHealth:
    checks: tuple = field(
        default_factory=lambda: default_checks(Config.SITE_HEALTH_HOST)
    )
    # async () -> a current Pokéstop id from the scanner DB, for the map check.
    map_stop: object = None
    host: str = field(default_factory=lambda: Config.SITE_HEALTH_HOST)
    states: dict = field(default_factory=dict)

    async def probe_all(self):
        """{name: None when healthy, else a short reason}."""
        results = await asyncio.gather(
            *(probe(url) for _, url in self.checks), self._probe_map()
        )
        named = {name: error for (name, _), error in zip(self.checks, results)}
        if self.map_stop is not None:
            named["Mapa"] = results[-1]
        return named

    async def _probe_map(self):
        """The map's page loads without its database (2026-09-26: ~2 h with no
        data and every other check green), so ask it for a real Pokéstop."""
        if self.map_stop is None:
            return None
        try:
            stop = await self.map_stop()
        except Exception:
            return "base de dados do scanner inacessível"
        if not stop:
            return "sem Pokéstops na base de dados do scanner"
        return await probe(f"http://{self.host}:1082/api/pokestop/{stop}")

    def record(self, results, now):
        """Fold one round of probes in. Returns [(action, name, state)]."""
        actions = []
        for name, error in results.items():
            state = self.states.setdefault(name, CheckState())
            action = decide(state, error, now)
            if action:
                actions.append((action, name, state))
        return actions


async def probe(url):
    try:
        async with get_session().get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS),
        ) as response:
            if response.status >= 400:
                return f"HTTP {response.status}"
            return None
    except asyncio.TimeoutError:
        return f"sem resposta em {TIMEOUT_SECONDS}s"
    except aiohttp.ClientError as e:
        return type(e).__name__


def decide(state, error, now):
    """Update `state` with one probe result; "down", "still_down", "recovered" or None."""
    if error is None:
        was_alerted = state.alerted_at is not None
        state.failures, state.last_error, state.alerted_at = 0, None, None
        return "recovered" if was_alerted else None

    state.failures += 1
    state.last_error = error
    if state.failures < DOWN_AFTER:
        return None
    if state.alerted_at is None:
        state.alerted_at = now
        return "down"
    if now - state.alerted_at >= REPEAT_AFTER:
        state.alerted_at = now
        return "still_down"
    return None


def message(action, name, state):
    if action == "recovered":
        return f"✅ **{name} voltou a responder.**"
    minutes = state.failures  # one probe per minute
    prefix = "⚠️" if action == "down" else "⚠️ Continua:"
    return f"{prefix} **{name} não responde** há {minutes} min ({state.last_error})."
