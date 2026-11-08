"""Bounded public map snapshots for member replies.

Only fixed, read-only queries against public scanner fields. The model receives
sanitized samples, never raw rows, encounter identifiers, accounts or arbitrary
SQL. Sections are collected independently so one failed query only marks that
section unavailable.
"""

import asyncio
import json
import math
import re
import time
import unicodedata
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from modules import scan_areas

_LISBON = ZoneInfo("Europe/Lisbon")
_TEAMS = {0: "Neutro", 1: "Mystic", 2: "Valor", 3: "Instinct"}
MAX_ROWS = 20
MAX_TEXT = 2000
QUERY_TIMEOUT = 5

_MAP = re.compile(
    r"\b(spawn\w*|aparec\w*|encontr\w*|pokemon|shiny|raid\w*|"
    r"ginasi\w*|gym\w*|ovo\w*|egg\w*|boss\w*|quest\w*|tarefa\w*|"
    r"pesquisa\w*|recompensa\w*|stardust|poeira\s+estelar|"
    r"mega\s+energy|lure\w*|isca\w*|modulo\w*|pokestop\w*|mapa|map|"
    r"numeros?|estatistic\w*|stats?|max\s+battle\w*|dynamax|gigantamax|"
    r"power\s+spot\w*|onde\s+fica\w*|coordenad\w*)\b"
)
_SPAWN = re.compile(
    r"\b(spawn\w*|aparec\w*|encontr\w*|pokemon|shiny|mapa|map|"
    r"numeros?|estatistic\w*|stats?)\b"
)
_RAID = re.compile(r"\b(raid\w*|ginasi\w*|gym\w*|ovo\w*|egg\w*|boss\w*)\b")
_QUEST = re.compile(
    r"\b(quest\w*|tarefa\w*|pesquisa\w*|recompensa\w*|stardust|"
    r"poeira\s+estelar|mega\s+energy)\b"
)
_LURE = re.compile(r"\b(lure\w*|isca\w*|modulo\w*)\b")
_MAX = re.compile(r"\b(max\s+battle\w*|dynamax|gigantamax|d?max|power\s+spot\w*)\b")
_PLACE = re.compile(
    r"\b(onde\s+(?:fica|e|esta)\w*|localiza\w*|coordenad\w*|morada|"
    r"pokestops?|gyms?|ginasios?|paragens?)\b"
)
# Words that never identify a Pokéstop or Gym by name.
_PLACE_STOPWORDS = frozenset(
    "onde fica ficam esta estao qual quais sabes sabem como chegar coordenadas "
    "coordenada localizacao morada pokestop pokestops gym gyms ginasio ginasios "
    "paragem paragens perto junto para pela pelo com uma umas uns sobre aqui "
    "que dos das nos nas tem ha hoje agora".split()
)

_REWARD_FILTERS = (
    (re.compile(r"\b(stardust|poeira\s+estelar)\b"), 3),
    (re.compile(r"\b(encounter|encontro)\b"), 7),
    (re.compile(r"\b(xl\s+candy|xl\s+doce)\b"), 13),
    (re.compile(r"\b(mega\s+energy|energia\s+mega)\b"), 12),
    (re.compile(r"\b(candy|doce)\b"), 4),
    (re.compile(r"\b(xp|experiencia)\b"), 1),
    (re.compile(r"\b(items?|itens)\b"), 2),
)


def _place_words(topic, species):
    """Name words for a Pokéstop/Gym lookup: at least 3 letters, not a
    question word, an area's name (it would match half the map) or a
    recognized species; at most five."""
    skip = {normalized(name) for _, name in species}
    for row in scan_areas.active_areas():
        skip.update(normalized(row["label"]).split())
    words = []
    for word in re.findall(r"[a-z0-9]{3,}", topic):
        if word in _PLACE_STOPWORDS or word in skip or word in words:
            continue
        words.append(word)
    return words[:5]


def _reward_types(topic):
    found = []
    for pattern, reward_type in _REWARD_FILTERS:
        if not pattern.search(topic) or reward_type in found:
            continue
        if reward_type == 4 and 13 in found:
            continue
        found.append(reward_type)
    return found


def _reward_label(reward_type, species):
    if reward_type == 1:
        return "XP"
    if reward_type == 2:
        return "Item"
    if reward_type == 3:
        return "Stardust"
    if reward_type == 4:
        return f"{species} Candy" if species else "Candy"
    if reward_type == 7:
        return f"{species} encounter" if species else "Pokémon encounter"
    if reward_type == 12:
        return f"{species} Mega Energy" if species else "Mega Energy"
    if reward_type == 13:
        return f"{species} XL Candy" if species else "XL Candy"
    return "Reward"


def normalized(text):
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(c)
    )


def _clean_text(value, limit):
    if not isinstance(value, str):
        return None
    value = unicodedata.normalize("NFKC", value)
    value = "".join(
        c if c in "\n\t" or not unicodedata.category(c).startswith("C") else " "
        for c in value
    )
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    if not value or len(value) > limit:
        return None
    return value


def _integer(value, low, high):
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            number = int(value.strip())
        except (TypeError, ValueError, OverflowError):
            return None
        if value.strip() != str(number):
            return None
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        number = int(value)
    elif isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            return None
        number = int(value)
    elif isinstance(value, int):
        number = value
    else:
        return None
    if not low <= number <= high:
        return None
    return number


def _coordinate(lat, lon):
    if isinstance(lat, bool) or isinstance(lon, bool):
        return None
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lon):
        return None
    box = scan_areas.bbox()
    if box is None:
        return None
    if not box[0] <= lat <= box[2] or not box[1] <= lon <= box[3]:
        return None
    return round(lat, 5), round(lon, 5)


def _timestamp(value, now, horizon=7 * 24 * 60 * 60):
    if isinstance(value, bool):
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(timestamp) or not now < timestamp <= now + horizon:
        return None
    return timestamp


def _clock(timestamp):
    return datetime.fromtimestamp(timestamp, _LISBON).strftime("%H:%M")


class PublicMapContext:
    """Fixed public map queries behind a small, validated snapshot contract."""

    def __init__(self, db, pokemon_names):
        self.db = db
        # Copy only public species names. Never retain the service, bot or Config.
        self._names = {}
        self._by_id = {}
        if isinstance(pokemon_names, dict):
            for key, value in pokemon_names.items():
                try:
                    pokemon_id = int(key)
                except (TypeError, ValueError):
                    continue
                name = _clean_text(value, 80) if isinstance(value, str) else None
                if not name or "\n" in name or not 1 <= pokemon_id <= 100000:
                    continue
                self._names.setdefault(normalized(name), (pokemon_id, name))
                self._by_id.setdefault(pokemon_id, name)

    def recognize(self, text):
        """Return at most five (id, display name) matches, longest first."""
        if not isinstance(text, str) or not text:
            return []
        normalized_text = normalized(text)
        found = []
        for name, (pokemon_id, display) in sorted(
            self._names.items(), key=lambda item: -len(item[0])
        ):
            if len(found) == 5:
                break
            if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", normalized_text):
                found.append((pokemon_id, display))
        return found

    def _display(self, pokemon_id):
        try:
            pokemon_id = int(pokemon_id)
        except (TypeError, ValueError):
            return None
        return self._by_id.get(pokemon_id)

    async def _rows(self, query, params):
        rows = await self.db.get_data_from_database(query, params=params)
        return rows if isinstance(rows, (list, tuple)) else []

    async def snapshot(self, text):
        """Return a bounded snapshot for the relevant sections, or None."""
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            return None
        await scan_areas.ensure()
        box = scan_areas.bbox()
        area = scan_areas.labels(" / ")
        if box is None or not area:
            return None
        topic = normalized(text)
        species = self.recognize(text)
        sections = []
        tasks = []
        if _SPAWN.search(topic) or (
            species
            and not (_RAID.search(topic) or _QUEST.search(topic) or _LURE.search(topic))
        ):
            tasks.append(self._section("spawns", self._spawns(species)))
        if _RAID.search(topic):
            tasks.append(self._section("raids", self._raids(species)))
        if _QUEST.search(topic):
            tasks.append(
                self._section("quests", self._quests(species, _reward_types(topic)))
            )
        if _LURE.search(topic):
            tasks.append(self._section("lures", self._lures()))
        if _MAX.search(topic):
            tasks.append(self._section("maxbattles", self._max_battles(species)))
        words = _place_words(topic, species)
        if _PLACE.search(topic) and words:
            tasks.append(self._section("places", self._places(words)))
        if not tasks:
            return None
        try:
            sections = await asyncio.wait_for(
                asyncio.gather(*tasks), timeout=QUERY_TIMEOUT
            )
        except (asyncio.TimeoutError, Exception):
            return {
                "area": area,
                "bbox": list(box),
                "generated_at": datetime.now(_LISBON).isoformat(timespec="seconds"),
                "status": "unavailable",
                "sections": [],
            }
        payload = {
            "area": area,
            "bbox": list(box),
            "generated_at": datetime.now(_LISBON).isoformat(timespec="seconds"),
            "note": "Amostra pública limitada; não é um inventário completo.",
            "sections": sections,
        }
        if len(json.dumps(payload, ensure_ascii=False)) > 24000:
            return None
        return payload

    async def _section(self, kind, coroutine):
        try:
            return await coroutine
        except Exception:
            return {"kind": kind, "status": "unavailable"}

    async def _spawns(self, species):
        try:
            box = scan_areas.bbox()
            if box is None:
                return {"kind": "spawns", "status": "unavailable"}
            now = time.time()
            if species:
                ids = [pokemon_id for pokemon_id, _ in species]
                placeholders = ", ".join(["%s"] * len(ids))
                rows = await self._rows(
                    "SELECT pokemon_id, lat, lon, expire_timestamp FROM pokemon "
                    "WHERE seen_type = 'encounter' "
                    "AND expire_timestamp_verified = 1 "
                    "AND expire_timestamp > UNIX_TIMESTAMP() "
                    "AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s "
                    f"AND pokemon_id IN ({placeholders}) "
                    "ORDER BY expire_timestamp ASC LIMIT 21",
                    (box[0], box[2], box[1], box[3], *ids),
                )
                locations = []
                for row in rows[:MAX_ROWS]:
                    if not isinstance(row, dict):
                        continue
                    pokemon_id = _integer(row.get("pokemon_id"), 1, 100000)
                    point = _coordinate(row.get("lat"), row.get("lon"))
                    expires = _timestamp(row.get("expire_timestamp"), now)
                    display = self._display(pokemon_id)
                    if not display or not point or not expires:
                        continue
                    locations.append(
                        {
                            "species": display,
                            "lat": point[0],
                            "lon": point[1],
                            "expires_at": _clock(expires),
                        }
                    )
                return {
                    "kind": "spawns",
                    "status": "ok",
                    "recognized": True,
                    "truncated": len(rows) > MAX_ROWS,
                    "sample_count": len(locations),
                    "locations": locations,
                }
            rows = await self._rows(
                "SELECT pokemon_id, COUNT(*) AS count FROM pokemon "
                "WHERE seen_type = 'encounter' "
                "AND expire_timestamp_verified = 1 "
                "AND expire_timestamp > UNIX_TIMESTAMP() "
                "AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s "
                "GROUP BY pokemon_id ORDER BY count DESC, pokemon_id ASC LIMIT 20",
                (box[0], box[2], box[1], box[3]),
            )
            groups = []
            for row in rows[:MAX_ROWS]:
                if not isinstance(row, dict):
                    continue
                pokemon_id = _integer(row.get("pokemon_id"), 1, 100000)
                count = _integer(row.get("count"), 1, 10**7)
                display = self._display(pokemon_id)
                if display and count:
                    groups.append({"species": display, "count": count})
            return {
                "kind": "spawns",
                "status": "ok",
                "recognized": False,
                "truncated": len(rows) >= MAX_ROWS,
                "groups": groups,
            }
        except Exception:
            return {"kind": "spawns", "status": "unavailable"}

    async def _raids(self, species):
        try:
            box = scan_areas.bbox()
            if box is None:
                return {"kind": "raids", "status": "unavailable"}
            now = time.time()
            ids = [pokemon_id for pokemon_id, _ in species]
            species_filter = ""
            params = [box[0], box[2], box[1], box[3]]
            if ids:
                placeholders = ", ".join(["%s"] * len(ids))
                species_filter = (
                    f"AND (raid_pokemon_id IN ({placeholders}) "
                    "OR raid_pokemon_id IS NULL OR raid_pokemon_id = 0) "
                )
                params.extend(ids)
            rows = await self._rows(
                "SELECT name, lat, lon, raid_level, raid_pokemon_id, "
                "raid_end_timestamp, raid_battle_timestamp FROM gym "
                "WHERE deleted = 0 AND lat BETWEEN %s AND %s "
                "AND lon BETWEEN %s AND %s AND raid_level > 0 "
                "AND raid_end_timestamp > UNIX_TIMESTAMP() "
                f"{species_filter}"
                "ORDER BY raid_end_timestamp ASC LIMIT 20",
                tuple(params),
            )
            items = []
            for row in rows[:MAX_ROWS]:
                if not isinstance(row, dict):
                    continue
                point = _coordinate(row.get("lat"), row.get("lon"))
                ends = _timestamp(row.get("raid_end_timestamp"), now)
                level = _integer(row.get("raid_level"), 1, 10)
                if not point or not ends or not level:
                    continue
                try:
                    battle = float(row.get("raid_battle_timestamp"))
                except (TypeError, ValueError, OverflowError):
                    battle = None
                if battle is not None and not math.isfinite(battle):
                    battle = None
                boss_id = _integer(row.get("raid_pokemon_id"), 1, 100000)
                boss = self._display(boss_id)
                active = bool(boss and battle is not None and battle <= now)
                item = {
                    "name": _clean_text(row.get("name"), 80) or "Gym",
                    "lat": point[0],
                    "lon": point[1],
                    "level": level,
                    "status": "active" if active else "egg",
                    "species": boss if active else None,
                    "ends_at": _clock(ends),
                }
                if not active:
                    hatches = _timestamp(
                        row.get("raid_battle_timestamp"), now, 24 * 3600
                    )
                    if hatches:
                        item["hatches_at"] = _clock(hatches)
                items.append(item)
            return {
                "kind": "raids",
                "status": "ok",
                "truncated": len(rows) >= MAX_ROWS,
                "items": items,
            }
        except Exception:
            return {"kind": "raids", "status": "unavailable"}

    async def _lures(self):
        try:
            box = scan_areas.bbox()
            if box is None:
                return {"kind": "lures", "status": "unavailable"}
            now = time.time()
            rows = await self._rows(
                "SELECT name, lat, lon, lure_id, lure_expire_timestamp "
                "FROM pokestop WHERE deleted = 0 "
                "AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s "
                "AND lure_expire_timestamp > UNIX_TIMESTAMP() "
                "ORDER BY lure_expire_timestamp ASC LIMIT 20",
                (box[0], box[2], box[1], box[3]),
            )
            items = []
            for row in rows[:MAX_ROWS]:
                if not isinstance(row, dict):
                    continue
                point = _coordinate(row.get("lat"), row.get("lon"))
                expires = _timestamp(row.get("lure_expire_timestamp"), now)
                if not point or not expires:
                    continue
                items.append(
                    {
                        "name": _clean_text(row.get("name"), 80) or "Pokéstop",
                        "lat": point[0],
                        "lon": point[1],
                        "expires_at": _clock(expires),
                    }
                )
            return {
                "kind": "lures",
                "status": "ok",
                "truncated": len(rows) >= MAX_ROWS,
                "items": items,
            }
        except Exception:
            return {"kind": "lures", "status": "unavailable"}

    async def _quests(self, species, reward_types):
        try:
            box = scan_areas.bbox()
            if box is None:
                return {"kind": "quests", "status": "unavailable"}
            now = time.time()
            ids = [pokemon_id for pokemon_id, _ in species]
            rows = []
            for prefix in ("quest", "alternative_quest"):
                fields = (
                    "name, lat, lon, "
                    f"{prefix}_title, {prefix}_reward_type, "
                    f"{prefix}_pokemon_id, {prefix}_reward_amount, "
                    f"{prefix}_expiry"
                )
                species_filter = ""
                reward_filter = ""
                params = [box[0], box[2], box[1], box[3]]
                if ids:
                    placeholders = ", ".join(["%s"] * len(ids))
                    species_filter = f"AND {prefix}_pokemon_id IN ({placeholders}) "
                    params.extend(ids)
                if reward_types:
                    placeholders = ", ".join(["%s"] * len(reward_types))
                    reward_filter = f"AND {prefix}_reward_type IN ({placeholders}) "
                    params.extend(reward_types)
                result = await self._rows(
                    f"SELECT {fields} FROM pokestop WHERE deleted = 0 "
                    f"AND {prefix}_reward_type IS NOT NULL "
                    f"AND {prefix}_expiry > UNIX_TIMESTAMP() "
                    "AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s "
                    f"{species_filter}{reward_filter}"
                    f"ORDER BY {prefix}_expiry ASC LIMIT 20",
                    tuple(params),
                )
                for row in result:
                    if isinstance(row, dict):
                        rows.append((prefix, row))
            items = []
            for prefix, row in rows:
                point = _coordinate(row.get("lat"), row.get("lon"))
                expires = _timestamp(row.get(f"{prefix}_expiry"), now)
                if not point or not expires:
                    continue
                pokemon_id = _integer(row.get(f"{prefix}_pokemon_id"), 1, 100000)
                species_name = self._display(pokemon_id)
                reward_type = _integer(row.get(f"{prefix}_reward_type"), 0, 1000)
                items.append(
                    {
                        "stop": _clean_text(row.get("name"), 80) or "Pokéstop",
                        "lat": point[0],
                        "lon": point[1],
                        "title": _clean_text(row.get(f"{prefix}_title"), 120),
                        "reward_label": _reward_label(reward_type, species_name),
                        "species": species_name,
                        "amount": _integer(
                            row.get(f"{prefix}_reward_amount"), 0, 10**6
                        ),
                        "ar": prefix == "alternative_quest",
                        "expires_at": _clock(expires),
                        "_expiry": expires,
                    }
                )
            items.sort(key=lambda item: item["_expiry"])
            truncated = len(items) > MAX_ROWS
            for item in items:
                item.pop("_expiry", None)
            return {
                "kind": "quests",
                "status": "ok",
                "truncated": truncated,
                "items": items[:MAX_ROWS],
            }
        except Exception:
            return {"kind": "quests", "status": "unavailable"}

    async def _max_battles(self, species):
        """Live Max Battles at Power Spots (the map's station layer)."""
        try:
            box = scan_areas.bbox()
            if box is None:
                return {"kind": "maxbattles", "status": "unavailable"}
            now = time.time()
            params = [box[0], box[2], box[1], box[3]]
            species_filter = ""
            ids = [pokemon_id for pokemon_id, _ in species]
            if ids:
                placeholders = ", ".join(["%s"] * len(ids))
                species_filter = f"AND battle_pokemon_id IN ({placeholders}) "
                params.extend(ids)
            rows = await self._rows(
                "SELECT name, lat, lon, battle_level, battle_pokemon_id, battle_end "
                "FROM station WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s "
                "AND battle_pokemon_id > 0 AND battle_end > UNIX_TIMESTAMP() "
                "AND end_time > UNIX_TIMESTAMP() "
                f"{species_filter}"
                "ORDER BY battle_level DESC, battle_end ASC LIMIT 21",
                tuple(params),
            )
            items = []
            for row in rows[:MAX_ROWS]:
                if not isinstance(row, dict):
                    continue
                point = _coordinate(row.get("lat"), row.get("lon"))
                ends = _timestamp(row.get("battle_end"), now)
                level = _integer(row.get("battle_level"), 1, 10)
                boss = self._display(row.get("battle_pokemon_id"))
                if not point or not ends or not level or not boss:
                    continue
                items.append(
                    {
                        "name": _clean_text(row.get("name"), 80) or "Power Spot",
                        "lat": point[0],
                        "lon": point[1],
                        "level": level,
                        "species": boss,
                        "ends_at": _clock(ends),
                    }
                )
            return {
                "kind": "maxbattles",
                "status": "ok",
                "truncated": len(rows) > MAX_ROWS,
                "items": items,
            }
        except Exception:
            return {"kind": "maxbattles", "status": "unavailable"}

    async def _places(self, words):
        """Pokéstops and Gyms whose name holds the question's words: the
        name, the coordinates and, for a Gym, its team, as the map shows."""
        try:
            box = scan_areas.bbox()
            if box is None:
                return {"kind": "places", "status": "unavailable"}
            likes = " OR ".join(["name LIKE %s"] * len(words))
            patterns = [f"%{word}%" for word in words]
            found = []
            for table, extra in (("gym", ", team_id"), ("pokestop", "")):
                rows = await self._rows(
                    f"SELECT name, lat, lon{extra} FROM {table} WHERE deleted = 0 "
                    "AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s "
                    f"AND ({likes}) LIMIT 200",
                    (box[0], box[2], box[1], box[3], *patterns),
                )
                for row in rows:
                    if isinstance(row, dict):
                        found.append((table, row))
            need = max(1, (len(words) + 1) // 2)
            scored = []
            for table, row in found:
                name = _clean_text(row.get("name"), 80)
                point = _coordinate(row.get("lat"), row.get("lon"))
                if not name or not point:
                    continue
                score = sum(word in normalized(name) for word in words)
                if score < need:
                    continue
                item = {
                    "type": "gym" if table == "gym" else "pokestop",
                    "name": name,
                    "lat": point[0],
                    "lon": point[1],
                }
                if table == "gym":
                    team = _integer(row.get("team_id"), 0, 3)
                    item["team"] = _TEAMS.get(team, "Neutro")
                scored.append((-score, name, item))
            scored.sort(key=lambda entry: (entry[0], entry[1]))
            return {
                "kind": "places",
                "status": "ok",
                "truncated": len(scored) > 10,
                "items": [item for _, _, item in scored[:10]],
            }
        except Exception:
            return {"kind": "places", "status": "unavailable"}
