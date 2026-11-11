import aiohttp

from modules.config import Config
from modules.logging_mixin import LoggingMixin


class PoracleError(Exception):
    pass


class PoracleClient(LoggingMixin):
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

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json=None,
        params: dict | None = None,
        timeout: int = 15,
    ):
        url = f"{self.base_url}{path}"
        session = self._get_session()
        try:
            async with session.request(
                method,
                url,
                json=json,
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout),
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
        except (aiohttp.ClientError, TimeoutError) as e:
            detail = str(e) or type(e).__name__
            self._log(f"Poracle {method} {path} failed: {detail}")
            raise PoracleError(detail) from e

    # ---- Humans (channels and users) -----------------------------------------

    async def create_user(self, user_id: str | int, name: str, *, area: str) -> dict:
        """Create a Discord user with its initial area as a JSON-array string.

        Poracle creates the enabled human and default profile. Conflicts and
        failures propagate without restarting or modifying an existing user.
        """
        return await self._request(
            "POST",
            "/api/humans",
            json={
                "id": str(user_id),
                "name": name,
                "type": "discord:user",
                "area": area,
            },
        )

    # ---- Misc ---------------------------------------------------------------

    async def health(self) -> None:
        await self._request("GET", "/health", timeout=5)

    async def reload(self) -> None:
        await self._request("POST", "/api/reload")

    async def test_pokemon(self, webhook: dict, target: dict) -> dict:
        return await self._request(
            "POST",
            "/api/test",
            json={"type": "pokemon", "webhook": webhook, "target": target},
        )
