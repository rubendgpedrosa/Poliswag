import hashlib
import json
import logging
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from modules.config import Config

_LISBON = ZoneInfo("Europe/Lisbon")


def _parse_species_id(value) -> int | None:
    """Coerce a scanner pokemon id to a positive int, else None."""
    try:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        species = int(value)
    except (TypeError, ValueError):
        return None
    return species if species > 0 else None


def _lisbon_today():
    return datetime.now(_LISBON).date()


def _generated_lisbon_day(generated: str):
    """Lisbon calendar day of a quests.json ``generatedAt`` stamp."""
    try:
        dt = datetime.fromisoformat(generated.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(_LISBON).date()
    except Exception:
        return None


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

    async def export(self, force: bool = False) -> bool:
        """Build the quests payload and write it to disk.

        The write is skipped when the quest content is unchanged since the last
        export (compared via a content hash stored in the meta file) *and* the
        on-disk payload is present, valid and from today's Europe/Lisbon
        consumer day, so this is cheap to call on a timer. Pass ``force=True``
        to always rewrite.

        Each file is replaced atomically (temp file plus rename, payload
        before metadata); the pair itself is not atomic.

        Returns ``True`` when the files were (re)written, ``False`` when the
        content was unchanged and the write was skipped.
        """
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

        standard_rows = await qs.db.get_data_from_database(
            """
            SELECT name, lat, lon, url,
                   quest_title, quest_target, quest_reward_type,
                   quest_item_id, quest_pokemon_id, quest_reward_amount,
                   quest_pokemon_form_id, quest_rewards
            FROM pokestop
            WHERE quest_reward_type IS NOT NULL AND deleted = 0
              AND quest_expiry > UNIX_TIMESTAMP()
            """,
        )
        alternative_rows = await qs.db.get_data_from_database(
            """
            SELECT name, lat, lon, url,
                   alternative_quest_title         AS quest_title,
                   alternative_quest_target        AS quest_target,
                   alternative_quest_reward_type   AS quest_reward_type,
                   alternative_quest_item_id       AS quest_item_id,
                   alternative_quest_pokemon_id    AS quest_pokemon_id,
                   alternative_quest_reward_amount AS quest_reward_amount,
                   alternative_quest_pokemon_form_id AS quest_pokemon_form_id,
                   alternative_quest_rewards AS quest_rewards
            FROM pokestop
            WHERE alternative_quest_reward_type IS NOT NULL AND deleted = 0
              AND alternative_quest_expiry > UNIX_TIMESTAMP()
            """,
        )

        logging.info(
            f"QuestExporter: {len(standard_rows) + len(alternative_rows)} quest rows"
        )

        merged: dict[str, dict] = {}
        seen_stops: dict[str, set] = {}
        for rows in (standard_rows, alternative_rows):
            for row in rows:
                title = self._translate_title(
                    row["quest_title"] or "", row["quest_target"], translations
                )
                reward = self._map_reward(
                    row["quest_reward_type"],
                    row["quest_reward_amount"],
                    row["quest_item_id"],
                    row["quest_pokemon_id"],
                    pokemon_names,
                    item_names,
                )
                if row["quest_reward_type"] == 7:
                    self._encounter_variant(reward, row, masterfile)
                elif row["quest_reward_amount"] and int(row["quest_reward_amount"]) > 0:
                    reward["amount"] = int(row["quest_reward_amount"])
                parts = [reward["type"]]
                if (
                    reward["type"] in ("candy", "mega_energy", "xl_candy")
                    and "pokemonId" in reward
                ):
                    try:
                        parts.append(f"#{int(reward['pokemonId'])}")
                    except (TypeError, ValueError):
                        pass
                if "amount" in reward:
                    parts.append(str(reward["amount"]))
                reward_key = "|".join(parts)
                key = f"{title}|{reward_key}"
                if key not in merged:
                    merged[key] = {
                        "id": self._make_id(title, reward_key),
                        "title": title,
                        "category": self._categorize(title),
                        "reward": reward,
                        "pokestops": [],
                    }
                    seen_stops[key] = set()
                stop_key = (row["name"], float(row["lat"]), float(row["lon"]))
                if stop_key in seen_stops[key]:
                    continue
                seen_stops[key].add(stop_key)
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
        meta_path = output.with_name("quests-meta.json")
        content_hash = hashlib.md5(
            json.dumps(quests, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

        if not force and self._read_stored_hash(meta_path) == content_hash:
            if self._payload_usable(output, content_hash):
                logging.info(
                    f"QuestExporter: quest content unchanged ({len(quests)} quests), "
                    "skipping write"
                )
                return False
            logging.info(
                "QuestExporter: metadata matches but payload is missing, invalid "
                "or from a previous Lisbon day — rewriting"
            )

        generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        payload = {"quests": quests, "generatedAt": generated_at}
        # Each file is written atomically via a unique same-directory temp file
        # plus os.replace (per-file only). The payload/metadata pair is not a
        # transaction: a crash between the two leaves a fresh payload with a
        # stale meta, which the next export recovers by rewriting.
        self._write_json_atomic(output, payload)
        self._write_json_atomic(
            meta_path,
            {"generatedAt": generated_at, "contentHash": content_hash},
        )

        logging.info(f"QuestExporter: wrote {len(quests)} quests → {output}")
        return True

    @staticmethod
    def _read_stored_hash(meta_path: Path) -> str | None:
        try:
            with open(meta_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        value = data.get("contentHash")
        return value if isinstance(value, str) else None

    @staticmethod
    def _payload_usable(payload_path: Path, content_hash: str) -> bool:
        """Whether the on-disk payload already satisfies this export.

        True only when the payload exists, parses to an object with a quests
        list whose content hash matches, and whose ``generatedAt`` falls on
        today's Europe/Lisbon consumer day (the site ignores older days).
        Anything else means the next write must recover, even when the meta
        hash matches.
        """
        try:
            with open(payload_path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        quests = payload.get("quests")
        if not isinstance(quests, list):
            return False
        try:
            payload_hash = hashlib.md5(
                json.dumps(quests, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
        except (TypeError, ValueError):
            return False
        if payload_hash != content_hash:
            return False
        generated = payload.get("generatedAt")
        if not isinstance(generated, str) or not generated:
            return False
        return _generated_lisbon_day(generated) == _lisbon_today()

    @staticmethod
    def _write_json_atomic(target: Path, data) -> None:
        """Write JSON via a unique same-directory temp file plus replace.

        The temp file takes the destination's mode (or 0644 when the
        destination does not exist yet) so replacing a world-readable export
        keeps its readership. Cleans the temp file when the write fails so an
        interrupted export leaves the previous complete file in place.
        """
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            mode = stat.S_IMODE(os.stat(target).st_mode)
        except OSError:
            mode = 0o644
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.chmod(tmp_name, mode)
            os.replace(tmp_name, target)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    @staticmethod
    def _translate_title(key: str, target, translations: dict) -> str:
        translated = translations.get(key.lower(), "")
        if not translated:
            translated = key.replace("_", " ").replace("quest ", "").title()
        return translated.replace("{0}", str(target or ""))

    @staticmethod
    def _get_zone(lon) -> str:
        return "marinha" if float(lon) <= Config.MARINHA_LON_MAX else "leiria"

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
    def _encounter_variant(reward: dict, row: dict, masterfile: dict) -> None:
        """Retain the scanner's form/costume and keep different rewards apart."""
        pokemon_id = int(row["quest_pokemon_id"])
        reward["type"] = f"encounter_{pokemon_id}_unknown"
        reward["label"] += " (variante por confirmar)"
        details = masterfile.get("pokemon", {}).get(str(pokemon_id), {})
        details = details if isinstance(details, dict) else {}
        info = {}
        try:
            raw = row.get("quest_rewards")
            rewards = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(rewards, list):
                info = next(
                    (
                        r.get("info", {})
                        for r in rewards
                        if isinstance(r, dict) and r.get("type") == 7
                    ),
                    {},
                )
        except (ValueError, TypeError):
            pass
        info = info if isinstance(info, dict) else {}
        # Golbat omits zero-valued display fields from a valid encounter info.
        # A missing raw reward is different: its variant cannot be confirmed.
        try:
            if int(info.get("pokemon_id", 0)) != pokemon_id:
                return
            form = int(
                info.get(
                    "form_id", info.get("form", row.get("quest_pokemon_form_id") or 0)
                )
            )
            costume = int(info.get("costume_id", info.get("costume", 0)))
        except (TypeError, ValueError):
            return
        if form < 0 or costume < 0:
            return
        if form == details.get("defaultFormId"):
            form = 0
        reward.update(pokemonId=pokemon_id, formId=form, costumeId=costume)
        suffix = (f"_f{form}" if form else "") + (f"_c{costume}" if costume else "")
        reward["type"] = f"encounter_{pokemon_id}{suffix}"
        form_name = (
            details.get("forms", {}).get(str(form), {}).get("name") if form else None
        )
        variant = form_name or (f"Costume #{costume}" if costume else None)
        name = details.get("name", f"#{pokemon_id}")
        reward["label"] = f"{name}{f' ({variant})' if variant else ''} encounter"

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
        if reward_type == 3:
            return {
                "type": "stardust",
                "label": f"Stardust{f' x{amount}' if amount else ''}",
                "emoji": "✨",
            }
        if reward_type == 4:
            species = _parse_species_id(pokemon_id)
            suffix = f" x{amount}" if amount else ""
            if species is not None:
                name = pokemon_names.get(str(species), f"#{species}")
                return {
                    "type": "candy",
                    "label": f"{name} Candy{suffix}",
                    "emoji": "🍬",
                    "pokemonId": species,
                }
            return {
                "type": "candy",
                "label": f"Candy{suffix}",
                "emoji": "🍬",
            }
        if reward_type == 7:
            name = pokemon_names.get(str(pokemon_id), f"#{pokemon_id}")
            return {
                "type": f"encounter_{pokemon_id}",
                "label": f"{name} encounter",
                "emoji": "🎯",
            }
        if reward_type == 12:
            # Keep the website-compatible type exactly "mega_energy" (the
            # site's getRewardTypeFilter uses equality); species travels in
            # the additive pokemonId field and in the grouping/id key.
            species = _parse_species_id(pokemon_id)
            suffix = f" x{amount}" if amount else ""
            if species is not None:
                name = pokemon_names.get(str(species), f"#{species}")
                return {
                    "type": "mega_energy",
                    "label": f"{name} Mega Energy{suffix}",
                    "emoji": "⚡",
                    "pokemonId": species,
                }
            return {
                "type": "mega_energy",
                "label": f"Mega Energy{suffix}",
                "emoji": "⚡",
            }
        if reward_type == 13:
            species = _parse_species_id(pokemon_id)
            suffix = f" x{amount}" if amount else ""
            if species is not None:
                name = pokemon_names.get(str(species), f"#{species}")
                return {
                    "type": "xl_candy",
                    "label": f"{name} XL Candy{suffix}",
                    "emoji": "🍬",
                    "pokemonId": species,
                }
            return {
                "type": "xl_candy",
                "label": f"XL Candy{suffix}",
                "emoji": "🍬",
            }
        if reward_type == 1:
            return {
                "type": "xp",
                "label": f"XP{f' x{amount}' if amount else ''}",
                "emoji": "⭐",
            }
        return {"type": f"reward_{reward_type}", "label": "Reward", "emoji": "🎁"}
