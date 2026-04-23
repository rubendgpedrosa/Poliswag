import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from modules.config import Config

ITEM_EMOJI: dict[int, str] = {
    1: "⚪",
    2: "🔵",
    3: "🟡",
    701: "🍓",
    702: "🍌",
    703: "🍍",
    705: "🍍",
    706: "🌹",
}


class QuestExporter:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.output_path = Config.QUEST_JSON_OUTPUT

    async def export(self):
        qs = self.poliswag.quest_search

        translations = (qs.translationfile_data or {}).get("data", {})
        masterfile = qs.masterfile_data or {}
        pokemon_names = {
            k: (v.get("name", f"#{k}") if isinstance(v, dict) else str(v))
            for k, v in masterfile.get("pokemon", {}).items()
        }
        item_names = {
            k: (v.get("name", f"Item #{k}") if isinstance(v, dict) else str(v))
            for k, v in masterfile.get("items", {}).items()
        }

        standard_rows = await asyncio.to_thread(
            qs.db.get_data_from_database,
            """
            SELECT name, lat, lon, url,
                   quest_title, quest_target, quest_reward_type,
                   quest_item_id, quest_pokemon_id, quest_reward_amount
            FROM pokestop WHERE quest_reward_type IS NOT NULL
            """,
        )
        ar_rows = await asyncio.to_thread(
            qs.db.get_data_from_database,
            """
            SELECT name, lat, lon, url,
                   alternative_quest_title         AS quest_title,
                   alternative_quest_target        AS quest_target,
                   alternative_quest_reward_type   AS quest_reward_type,
                   alternative_quest_item_id       AS quest_item_id,
                   alternative_quest_pokemon_id    AS quest_pokemon_id,
                   alternative_quest_reward_amount AS quest_reward_amount
            FROM pokestop WHERE alternative_quest_reward_type IS NOT NULL
            """,
        )

        logging.info(
            f"QuestExporter: {len(standard_rows)} standard, {len(ar_rows)} AR rows"
        )

        merged: dict[str, dict] = {}
        for ar, rows in ((False, standard_rows), (True, ar_rows)):
            for row in rows:
                title = self._translate_title(
                    row["quest_title"] or "", row["quest_target"], translations
                )
                if ar:
                    title = f"[AR] {title}"
                reward = self._map_reward(
                    row["quest_reward_type"],
                    row["quest_reward_amount"],
                    row["quest_item_id"],
                    row["quest_pokemon_id"],
                    pokemon_names,
                    item_names,
                )
                key = f"{title}|{reward['type']}"
                if key not in merged:
                    merged[key] = {
                        "id": self._make_id(title, reward["type"]),
                        "title": title,
                        "category": self._categorize(title),
                        "reward": reward,
                        "pokestops": [],
                    }
                stop: dict = {
                    "name": row["name"] or "Unknown",
                    "location": {
                        "lat": float(row["lat"]),
                        "lng": float(row["lon"]),
                    },
                    "zone": self._get_zone(row["lon"]),
                    "done": False,
                }
                if row.get("url"):
                    stop["imageUrl"] = row["url"]
                merged[key]["pokestops"].append(stop)

        quests = []
        for entry in merged.values():
            entry["stopsCount"] = len(entry["pokestops"])
            quests.append(entry)
        quests.sort(key=lambda q: q["title"].lower())

        output = Path(self.output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload = {"quests": quests, "generatedAt": generated_at}
        with open(output, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        meta_path = output.with_name("quests-meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump({"generatedAt": generated_at}, f, ensure_ascii=False)

        logging.info(f"QuestExporter: wrote {len(quests)} quests → {output}")

    @staticmethod
    def _translate_title(key: str, target, translations: dict) -> str:
        translated = translations.get(key.lower(), "")
        if not translated:
            translated = key.replace("_", " ").replace("quest ", "").title()
        return translated.replace("{0}", str(target or ""))

    @staticmethod
    def _get_zone(lon) -> str:
        return "marinha" if "-8.9" in str(lon) else "leiria"

    @staticmethod
    def _categorize(title: str) -> str:
        t = title.lower()
        if re.search(r"throw|curve", t):
            return "throwing"
        if re.search(r"battle|raid|gym|defeat|team go rocket|go battle league", t):
            return "battling"
        if re.search(r"buddy|earn.*heart|give.*treat|walk.*km", t):
            return "buddy"
        if re.search(r"friend|gift|trade", t):
            return "buddy"
        if re.search(r"catch|hatch", t):
            return "catching"
        return "others"

    @staticmethod
    def _make_id(title: str, reward_type: str) -> str:
        return hashlib.md5(f"{title}|{reward_type}".encode()).hexdigest()[:12]

    @staticmethod
    def _map_reward(
        reward_type, amount, item_id, pokemon_id, pokemon_names, item_names
    ) -> dict:
        if reward_type == 2:
            name = item_names.get(str(item_id), f"Item #{item_id}")
            suffix = f" x{amount}" if amount and int(amount) > 1 else ""
            emoji = ITEM_EMOJI.get(int(item_id) if item_id else 0, "🎒")
            return {
                "type": f"item_{item_id}",
                "label": f"{name}{suffix}",
                "emoji": emoji,
            }
        if reward_type in (3, 4):
            return {
                "type": "stardust",
                "label": f"Stardust{f' x{amount}' if amount else ''}",
                "emoji": "✨",
            }
        if reward_type == 7:
            name = pokemon_names.get(str(pokemon_id), f"#{pokemon_id}")
            return {
                "type": f"encounter_{pokemon_id}",
                "label": f"{name} encounter",
                "emoji": "🎯",
            }
        if reward_type == 12:
            name = pokemon_names.get(str(pokemon_id), f"#{pokemon_id}")
            return {
                "type": "mega_energy",
                "label": f"Mega Energy{f' x{amount}' if amount else ''}",
                "emoji": "⚡",
            }
        if reward_type == 13:
            return {
                "type": "xl_candy",
                "label": f"XL Candy{f' x{amount}' if amount else ''}",
                "emoji": "🍬",
            }
        if reward_type == 1:
            return {
                "type": "xp",
                "label": f"XP{f' x{amount}' if amount else ''}",
                "emoji": "⭐",
            }
        return {"type": f"reward_{reward_type}", "label": "Reward", "emoji": "🎁"}
