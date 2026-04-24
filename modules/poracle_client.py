import aiohttp

from modules.config import Config


class PoracleError(Exception):
    pass


class PoracleClient:
    """Thin async wrapper over the Poracle-NG REST API.

    Scope is intentionally pokemon-only today — the only surface the
    notifications cog exposes to moderators.
    """

    def __init__(
        self, poliswag, base_url: str | None = None, secret: str | None = None
    ):
        self.poliswag = poliswag
        self.base_url = (base_url or Config.PORACLE_API_URL).rstrip("/")
        self.secret = secret if secret is not None else Config.PORACLE_API_SECRET
        self._session: aiohttp.ClientSession | None = None

    def _log(self, msg, level="ERROR"):
        self.poliswag.utility.log_to_file(msg, level)

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-Poracle-Secret": self.secret} if self.secret else None
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _request(self, method: str, path: str, *, json=None, timeout: int = 15):
        url = f"{self.base_url}{path}"
        session = self._get_session()
        try:
            async with session.request(
                method, url, json=json, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    self._log(f"Poracle {method} {path} -> {resp.status}: {body[:200]}")
                    raise PoracleError(f"{resp.status}: {body[:200]}")
                if resp.status == 204:
                    return None
                ctype = resp.headers.get("Content-Type", "")
                if "application/json" in ctype:
                    return await resp.json()
                text = await resp.text()
                return text if text else None
        except aiohttp.ClientError as e:
            self._log(f"Poracle {method} {path} failed: {e}")
            raise PoracleError(str(e)) from e

    # ---- Humans (channels) ---------------------------------------------------

    async def get_human(self, human_id: str | int) -> dict | None:
        try:
            return await self._request("GET", f"/api/humans/one/{human_id}")
        except PoracleError:
            return None

    async def create_channel(self, channel_id: str | int, name: str) -> dict:
        return await self._request(
            "POST",
            "/api/humans",
            json={"id": str(channel_id), "name": name, "type": "discord:channel"},
        )

    async def start(self, human_id: str | int) -> None:
        await self._request("POST", f"/api/humans/{human_id}/start")

    async def stop(self, human_id: str | int) -> None:
        await self._request("POST", f"/api/humans/{human_id}/stop")

    async def set_areas(self, human_id: str | int, areas: list[str]) -> None:
        await self._request("POST", f"/api/humans/{human_id}/setAreas", json=areas)

    # ---- Pokemon tracking ----------------------------------------------------

    async def list_pokemon_tracking(self, human_id: str | int) -> list[dict]:
        data = await self._request("GET", f"/api/tracking/pokemon/{human_id}")
        if isinstance(data, dict):
            rules = data.get("pokemon", [])
            return rules if isinstance(rules, list) else []
        return data if isinstance(data, list) else []

    async def add_pokemon_tracking(
        self, human_id: str | int, rules: list[dict] | dict
    ) -> dict | list:
        body = rules if isinstance(rules, list) else [rules]
        return await self._request(
            "POST", f"/api/tracking/pokemon/{human_id}", json=body
        )

    async def delete_pokemon_tracking_uid(
        self, human_id: str | int, uid: str | int
    ) -> None:
        await self._request("DELETE", f"/api/tracking/pokemon/{human_id}/byUid/{uid}")

    # ---- Misc ---------------------------------------------------------------

    async def reload(self) -> None:
        await self._request("POST", "/api/reload")

    async def test_pokemon(self, webhook: dict, target: dict) -> dict:
        return await self._request(
            "POST",
            "/api/test",
            json={"type": "pokemon", "webhook": webhook, "target": target},
        )
