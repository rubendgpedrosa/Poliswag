"""Released shiny forms plus exact live confirmations, shared with the map.

Leek Duck identifies the released form and release date. PoGoAPI adds the
species' wild encounter eligibility, never permission for all its variants.
Unmapped costumes stay out until an exact live shiny encounter confirms them.
"""

import asyncio
import re
import time
from datetime import date

import aiohttp

from modules.http_client import get_session

RELEASE_URL = "https://leekduck.com/shiny/pms.json"
WILD_URL = "https://pogoapi.net/api/v1/shiny_pokemon.json"
REFRESH_SECONDS = 86400
RETRY_SECONDS = 3600
USER_AGENT = "Mozilla/5.0 (compatible; PoGoLeiriaAvailability/1.0)"


def _normal(value):
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def released_forms(releases, wild, masterfile):
    """Exact, unambiguous forms only; (pokemon, form, costume=0, date, wild).

    A plain species entry names its default form, never every form. Its 0
    alias follows the scanner's absent-form convention. Costume .c identifiers
    use a different enum and cannot be treated as form IDs. Invalid/future dates
    cannot grant access: SQL checks dates, live sightings handle release day.
    """
    pokemon = masterfile.get("pokemon", {})
    result = {}
    for entry in releases:
        pid = entry.get("dex")
        if type(pid) is not int or pid < 1:
            continue
        token = entry.get("aa_fn")
        if not isinstance(token, str):
            continue
        match = re.fullmatch(rf"pm{pid}(?:\.f([A-Z0-9_]+))?", token)
        if not match:
            continue
        try:
            released = date.fromisoformat(entry["released_date"].replace("/", "-"))
        except (KeyError, ValueError, AttributeError):
            continue
        details = pokemon.get(str(pid)) or {}
        forms = details.get("forms") or {}
        suffix = match.group(1)
        if suffix:
            key = _normal(suffix)
            prefix = _normal(details.get("name", ""))
            short = key[len(prefix) :] if prefix and key.startswith(prefix) else key
            candidates = [
                int(fid)
                for fid, form in forms.items()
                if _normal(form.get("name", "")) in {key, short}
            ]
            if len(candidates) != 1:
                continue
        elif details:
            default = details.get("defaultFormId")
            if default is None or (forms and str(default) not in forms):
                continue
            candidates = [int(default)]
            if default:
                candidates.append(0)
        else:
            continue
        can_wild = wild.get(str(pid), {}).get("found_wild") is True
        # These species have event-dependent shiny availability. A historical
        # release is not a current enablement signal; require fresh sightings.
        if pid in {201, 235, 808}:
            can_wild = False
        for fid in candidates:
            key = (pid, fid, 0)
            row = (pid, fid, 0, released, int(can_wild))
            if key not in result or released < result[key][3]:
                result[key] = row
    return sorted(result.values())


class ShinyAvailability:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.db = poliswag.db
        self.next_refresh = 0

    async def _fetch(self, url):
        async with get_session().get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            response.raise_for_status()
            return await response.json(content_type=None)

    async def tick(self):
        from modules.config import Config

        # Statistics omit costumes. Keep exact live confirmations separately so
        # a shiny costume never enables another costume or the ordinary variant.
        await self.db.execute_query_to_database(
            "INSERT INTO shiny_confirmed_variant (pokemon_id, form_id, costume_id, last_seen) "
            f"SELECT pokemon_id, COALESCE(form, 0), COALESCE(costume, 0), MAX(first_seen_timestamp) FROM {Config.DB_SCANNER_NAME}.pokemon "
            "WHERE seen_type = 'encounter' AND shiny = 1 AND COALESCE(costume,0) <> 255 "
            "GROUP BY pokemon_id, form, costume "
            "ON DUPLICATE KEY UPDATE last_seen = GREATEST(last_seen, VALUES(last_seen))"
        )
        now = time.monotonic()
        if now < self.next_refresh:
            return
        self.next_refresh = now + RETRY_SECONDS
        try:
            releases, wild = await asyncio.gather(
                self._fetch(RELEASE_URL), self._fetch(WILD_URL)
            )
            if (
                not isinstance(releases, list)
                or len(releases) < 800
                or not all(isinstance(row, dict) for row in releases)
                or not isinstance(wild, dict)
                or len(wild) < 300
                or not all(isinstance(row, dict) for row in wild.values())
            ):
                raise ValueError("Incomplete shiny availability sources")
            masterfile = self.poliswag.quest_search.masterfile_data
            rows = released_forms(releases, wild, masterfile or {})
            if len(rows) < 500:
                raise ValueError("Incomplete shiny form mapping")
            placeholders = ",".join(["(%s,%s,%s,%s,%s,UTC_TIMESTAMP())"] * len(rows))
            await self.db.execute_transaction(
                [
                    ("DELETE FROM shiny_release", None),
                    (
                        "INSERT INTO shiny_release (pokemon_id,form_id,costume_id,released_date,wild_eligible,synced_at) VALUES "
                        + placeholders,
                        tuple(value for row in rows for value in row),
                    ),
                ]
            )
            self.next_refresh = now + REFRESH_SECONDS
        except Exception as error:
            self.poliswag.utility.log_to_file(
                f"Shiny availability refresh failed; keeping previous data: {error}",
                "ERROR",
            )
