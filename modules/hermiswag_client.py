"""Public tag replies through Hermiswag, the PoGoLeiria agent on this host.

Hermiswag is its own Hermes install (user `hermiswag`, fresh state, Sonnet
5.5 medium) with no shell, file or memory tools: Poliswag sends it the same
trusted rules plus the member's question and the validated public map
snapshot as untrusted data, exactly as the worker path does. The conversation
is the Discord channel's: one Hermes session per channel per day
(`X-Hermes-Session-Key`), and each turn also carries the channel's recent
messages, so a follow-up such as "e ele é útil?" keeps its context.
Nothing here retains error bodies; failures return None and the caller falls
back to the worker path.
"""

import asyncio
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp

_LISBON = ZoneInfo("Europe/Lisbon")
_RUN_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
TERMINAL = ("completed", "failed", "cancelled", "interrupted")


class HermiswagClient:
    POLL_INTERVAL = 0.5

    def __init__(self, url, key, *, timeout=50):
        self.url = (url or "").rstrip("/")
        self.key = key or ""
        self.timeout = timeout

    @property
    def enabled(self):
        return bool(self.url and self.key)

    @staticmethod
    def conversation(channel_id):
        """One conversation per Discord channel per Lisbon day."""
        day = datetime.now(_LISBON).strftime("%Y%m%d")
        return f"hermiswag:channel:{int(channel_id)}:{day}"

    async def ask(self, session, instructions, prompt, timeout=None):
        """The run's final text, or None on any failure or timeout."""
        if not self.enabled or not session:
            return None
        try:
            headers = {
                "Authorization": f"Bearer {self.key}",
                "X-Hermes-Session-Key": str(session),
            }
            async with asyncio.timeout(timeout or self.timeout):
                async with aiohttp.ClientSession(
                    headers=headers, trust_env=False
                ) as session:
                    data = await self._json(
                        session,
                        "POST",
                        "/v1/runs",
                        {"input": prompt, "instructions": instructions},
                    )
                    run_id = data.get("run_id")
                    if not isinstance(run_id, str) or not _RUN_ID.match(run_id):
                        return None
                    try:
                        while True:
                            data = await self._json(
                                session, "GET", f"/v1/runs/{run_id}"
                            )
                            if data.get("status") in TERMINAL:
                                break
                            await asyncio.sleep(self.POLL_INTERVAL)
                    except asyncio.CancelledError:
                        await self._stop(session, run_id)
                        raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError):
            return None
        if data.get("status") != "completed" or data.get("partial"):
            return None
        text = data.get("output")
        return text.strip() if isinstance(text, str) and text.strip() else None

    async def _json(self, session, method, path, payload=None):
        async with session.request(
            method,
            self.url + path,
            json=payload,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            if response.status not in (200, 202):
                raise ValueError("unavailable")
            data = await response.json()
            if not isinstance(data, dict):
                raise ValueError("unavailable")
            return data

    async def _stop(self, session, run_id):
        try:
            await self._json(session, "POST", f"/v1/runs/{run_id}/stop", {})
        except Exception:
            pass


def reply_text(raw):
    """{"reply": "..."} as the rules ask, or the plain answer if the model
    skipped the JSON; safe_reply screens it either way."""
    if not isinstance(raw, str) or len(raw) > 4096:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return text or None
    if isinstance(data, dict) and isinstance(data.get("reply"), str):
        return data["reply"]
    return None
