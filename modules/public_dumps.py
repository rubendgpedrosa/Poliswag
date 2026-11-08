"""Public data dumps for tag requests: CSV files of what the public map shows.

"lista de Pokéstops de Leiria", "dump das Quests", "exporta os Gyms": fixed,
read-only, parameterized queries inside the registry areas' box, the same
public fields the map shows (name, coordinates, area, Gym team, Quest reward,
Max Battle boss), sent as a CSV attachment. No model is involved, so nothing
here can be steered by the question beyond picking a kind and an area.
"""

import csv
import io
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from modules import scan_areas
from modules.public_map_context import (
    _clean_text,
    _coordinate,
    _integer,
    _reward_label,
    _timestamp,
    normalized,
)

_LISBON = ZoneInfo("Europe/Lisbon")
MAX_ROWS = 5000
_ASK = re.compile(
    r"\b(lista\w*|listagem|dump|exporta\w*|export|csv|ficheiro|tabela|todas?\s+as|todos?\s+os)\b"
)
_KINDS = (
    ("quests", re.compile(r"\b(quests?|tarefas?|pesquisas?)\b")),
    ("maxbattles", re.compile(r"\b(max\s+battles?|power\s+spots?|dynamax|dmax)\b")),
    ("gyms", re.compile(r"\b(gyms?|ginasios?)\b")),
    ("pokestops", re.compile(r"\b(pokestops?|paragens?|stops?)\b")),
)
_TEAMS = {0: "Neutro", 1: "Mystic", 2: "Valor", 3: "Instinct"}
_LABELS = {
    "pokestops": "Pokéstops",
    "gyms": "Gyms",
    "quests": "Quests",
    "maxbattles": "Max Battles",
}


def dump_request(text):
    """(kind, area_key or None) when the question asks for a list, else None."""
    if not isinstance(text, str):
        return None
    topic = normalized(text)
    if not _ASK.search(topic):
        return None
    kind = next((name for name, pattern in _KINDS if pattern.search(topic)), None)
    if kind is None:
        return None
    area = None
    for row in scan_areas.active_areas():
        words = normalized(row["label"])
        if re.search(rf"(?<!\w){re.escape(words)}(?!\w)", topic) or re.search(
            rf"(?<!\w){re.escape(row['area_key'])}(?!\w)", topic
        ):
            area = row["area_key"]
            break
    return kind, area


class PublicDumps:
    def __init__(self, db, map_context):
        self.db = db
        self.map_context = map_context

    async def build(self, kind, area=None):
        """(filename, CSV bytes, row count, caption) or None."""
        await scan_areas.ensure()
        box = scan_areas.area_bbox(area) if area else scan_areas.bbox()
        if box is None:
            return None
        rows = await getattr(self, f"_{kind}")(box)
        rows = [r for r in rows if r and (area is None or r["area"] == area)]
        if not rows:
            return None
        rows = rows[:MAX_ROWS]
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        where = scan_areas.label(area) if area else "todas as áreas"
        stamp = datetime.now(_LISBON).strftime("%Y%m%d-%H%M")
        name = f"{kind}-{area or 'todas'}-{stamp}.csv"
        caption = (
            f"📋 {_LABELS[kind]} em {where}: {len(rows)} "
            f"{'linha' if len(rows) == 1 else 'linhas'}, dados públicos do mapa "
            f"às {datetime.now(_LISBON).strftime('%H:%M')}."
        )
        return name, out.getvalue().encode("utf-8-sig"), len(rows), caption

    async def _query(self, sql, box):
        rows = await self.db.get_data_from_database(
            sql, params=(box[0], box[2], box[1], box[3])
        )
        return rows if isinstance(rows, (list, tuple)) else []

    @staticmethod
    def _place(row):
        point = _coordinate(row.get("lat"), row.get("lon"))
        if not point:
            return None
        area = scan_areas.area_for(point[0], point[1])
        if area is None:
            return None
        return point, area

    async def _pokestops(self, box):
        out = []
        for row in await self._query(
            "SELECT name, lat, lon FROM pokestop WHERE deleted = 0 "
            "AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s "
            f"ORDER BY name LIMIT {MAX_ROWS + 1}",
            box,
        ):
            place = self._place(row)
            if place:
                out.append(
                    {
                        "nome": _clean_text(row.get("name"), 120) or "Pokéstop",
                        "lat": place[0][0],
                        "lon": place[0][1],
                        "area": place[1],
                    }
                )
        return out

    async def _gyms(self, box):
        out = []
        for row in await self._query(
            "SELECT name, lat, lon, team_id FROM gym WHERE deleted = 0 "
            "AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s "
            f"ORDER BY name LIMIT {MAX_ROWS + 1}",
            box,
        ):
            place = self._place(row)
            if place:
                out.append(
                    {
                        "nome": _clean_text(row.get("name"), 120) or "Gym",
                        "lat": place[0][0],
                        "lon": place[0][1],
                        "area": place[1],
                        "equipa": _TEAMS.get(
                            _integer(row.get("team_id"), 0, 3), "Neutro"
                        ),
                    }
                )
        return out

    async def _quests(self, box):
        now = time.time()
        out = []
        for prefix in ("quest", "alternative_quest"):
            for row in await self._query(
                "SELECT name, lat, lon, "
                f"{prefix}_reward_type AS reward_type, {prefix}_pokemon_id AS pokemon_id, "
                f"{prefix}_reward_amount AS amount, {prefix}_expiry AS expiry "
                f"FROM pokestop WHERE deleted = 0 AND {prefix}_reward_type IS NOT NULL "
                f"AND {prefix}_expiry > UNIX_TIMESTAMP() "
                "AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s "
                f"ORDER BY name LIMIT {MAX_ROWS + 1}",
                box,
            ):
                place = self._place(row)
                expires = _timestamp(row.get("expiry"), now)
                if not place or not expires:
                    continue
                species = self.map_context._display(row.get("pokemon_id"))
                reward = _reward_label(
                    _integer(row.get("reward_type"), 0, 1000), species
                )
                amount = _integer(row.get("amount"), 0, 10**6)
                out.append(
                    {
                        "pokestop": _clean_text(row.get("name"), 120) or "Pokéstop",
                        "lat": place[0][0],
                        "lon": place[0][1],
                        "area": place[1],
                        "recompensa": (
                            f"{amount}× {reward}" if amount and amount > 1 else reward
                        ),
                        "ar": "sim" if prefix == "alternative_quest" else "não",
                        "expira": datetime.fromtimestamp(expires, _LISBON).strftime(
                            "%d/%m %H:%M"
                        ),
                    }
                )
        return out

    async def _maxbattles(self, box):
        now = time.time()
        out = []
        for row in await self._query(
            "SELECT name, lat, lon, battle_level, battle_pokemon_id, battle_end "
            "FROM station WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s "
            "AND battle_pokemon_id > 0 AND battle_end > UNIX_TIMESTAMP() "
            "AND end_time > UNIX_TIMESTAMP() "
            f"ORDER BY battle_level DESC, name LIMIT {MAX_ROWS + 1}",
            box,
        ):
            place = self._place(row)
            ends = _timestamp(row.get("battle_end"), now)
            boss = self.map_context._display(row.get("battle_pokemon_id"))
            if not place or not ends or not boss:
                continue
            out.append(
                {
                    "power_spot": _clean_text(row.get("name"), 120) or "Power Spot",
                    "lat": place[0][0],
                    "lon": place[0][1],
                    "area": place[1],
                    "boss": boss,
                    "nivel": _integer(row.get("battle_level"), 1, 10) or "",
                    "termina": datetime.fromtimestamp(ends, _LISBON).strftime(
                        "%d/%m %H:%M"
                    ),
                }
            )
        return out
