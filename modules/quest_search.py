import discord
import requests
from datetime import datetime, timedelta
import json
from modules.config import Config
from modules.database_connector import DatabaseConnector
import logging


HTTP_TIMEOUT_SECONDS = 15


def _cache_fresh(entry, max_age: timedelta) -> bool:
    if not entry:
        return False
    return datetime.now() - datetime.fromisoformat(entry["date"]) < max_age


_QUEST_FIELD_NAMES = (
    "title",
    "target",
    "reward_type",
    "reward_amount",
    "pokemon_id",
    "item_id",
)


def _quest_fields(quest: dict) -> dict:
    """Return a normalized view of quest fields regardless of AR/standard schema.

    Each column is checked independently: a row may have ``alternative_quest_*``
    for some fields and ``quest_*`` for others, so we pick per-field whether the
    alternative key is present.
    """
    out = {}
    for name in _QUEST_FIELD_NAMES:
        alt_key = f"alternative_quest_{name}"
        key = alt_key if alt_key in quest else f"quest_{name}"
        out[name] = quest.get(key)
    if out["title"] is None:
        out["title"] = ""
    return out


class QuestSearch:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.db = DatabaseConnector(Config.DB_SCANNER_NAME)
        self.POKEMON_NAME_FILE = Config.POKEMON_NAME_FILE
        self.ITEM_NAME_FILE = Config.ITEM_NAME_FILE
        self.UI_ICONS_URL = Config.UI_ICONS_URL

        self.masterfile_data = None
        self.translationfile_data = None
        self.quest_data = None
        self.alternative_quest_data = None
        self.pokemon_name_map = {}
        self.item_name_map = {}

        self.load_translation_data()
        self.load_masterfile_data()
        self.generate_pokemon_item_name_map()

    def load_translation_data(self):
        if _cache_fresh(self.translationfile_data, timedelta(hours=24)):
            return self.translationfile_data

        try:
            response = requests.get(
                Config.TRANSLATIONFILE_ENDPOINT, timeout=HTTP_TIMEOUT_SECONDS
            )
            response.raise_for_status()

            translation_data = response.json().get("data", [])
            translated_dict = {
                translation_data[i].strip('"'): translation_data[i + 1].strip('"')
                for i in range(0, len(translation_data), 2)
            }
            self.translationfile_data = {
                "data": translated_dict,
                "date": datetime.now().isoformat(),
            }
            return self.translationfile_data
        except requests.exceptions.RequestException as e:
            logging.error(f"Error loading translation data: {e}")
            return None

    def load_masterfile_data(self):
        if _cache_fresh(self.masterfile_data, timedelta(hours=24)):
            return False

        try:
            response = requests.get(
                Config.MASTERFILE_ENDPOINT, timeout=HTTP_TIMEOUT_SECONDS
            )
            response.raise_for_status()

            masterfile_json = response.json()
            self.masterfile_data = {
                key: value
                for key, value in masterfile_json.items()
                if key in ["items", "questRewardTypes", "pokemon"]
            }
            self.masterfile_data["date"] = datetime.now().isoformat()
            return True
        except requests.exceptions.RequestException as e:
            logging.error(f"Error loading masterfile data: {e}")
            return False

    def generate_pokemon_item_name_map(self):
        if not self.masterfile_data or "pokemon" not in self.masterfile_data:
            logging.error("Error: Masterfile data or 'pokemon' key is missing.")
            return

        # Add debugging to check structure
        if "pokemon" in self.masterfile_data:
            sample_pokemon = next(iter(self.masterfile_data["pokemon"].items()), None)
            logging.info(f"Sample pokemon structure: {sample_pokemon}")

        if "items" in self.masterfile_data:
            sample_item = next(iter(self.masterfile_data["items"].items()), None)
            logging.info(f"Sample item structure: {sample_item}")

        self.pokemon_name_map = {}
        for pokemon_id, details in self.masterfile_data["pokemon"].items():
            if isinstance(details, dict) and "name" in details:
                self.pokemon_name_map[pokemon_id] = details["name"].lower()
            else:
                # Handle the case where details is a string or doesn't have a "name" key
                logging.warning(
                    f"Unexpected pokemon data format for ID {pokemon_id}: {details}"
                )

        self.item_name_map = {}
        for item_id, details in self.masterfile_data["items"].items():
            if isinstance(details, dict) and "name" in details:
                self.item_name_map[item_id] = details["name"].lower()
            elif isinstance(details, str):
                # If details is directly a string, use it as the name
                self.item_name_map[item_id] = details.lower()
            else:
                logging.warning(
                    f"Unexpected item data format for ID {item_id}: {details}"
                )

        try:
            with open(self.POKEMON_NAME_FILE, "w") as file:
                json.dump(self.pokemon_name_map, file, indent=4)
            with open(self.ITEM_NAME_FILE, "w") as file:
                json.dump(self.item_name_map, file, indent=4)
        except Exception as e:
            logging.error(f"Error generating pokemon/item name map: {e}")

    def get_pokemon_id_by_pokemon_name_map(self, search_keyword):
        if not self.pokemon_name_map:
            with open(self.POKEMON_NAME_FILE, "r") as file:
                self.pokemon_name_map = json.load(file)

        if not self.pokemon_name_map:
            return []

        matching_ids = []
        search_keyword_lower = search_keyword.lower()

        for pokemon_id, pokemon_name in self.pokemon_name_map.items():
            if search_keyword_lower in pokemon_name:
                matching_ids.append(pokemon_id)
        return matching_ids

    def get_item_id_by_item_name_map(self, search_keyword):
        if not self.item_name_map:
            with open(self.ITEM_NAME_FILE, "r") as file:
                self.item_name_map = json.load(file)

        if not self.item_name_map:
            return []

        matching_ids = []
        search_keyword_lower = search_keyword.lower()

        for item_id, item_name in self.item_name_map.items():
            if search_keyword_lower in item_name:
                matching_ids.append(item_id)
        return matching_ids

    def get_quest_data(self):
        if self.quest_data and datetime.now() - datetime.fromisoformat(
            self.quest_data["date"]
        ) < timedelta(hours=1):
            return self.quest_data

        quest_data = self.db.get_data_from_database(
            """
            SELECT name, lat, lon, url, quest_title, quest_pokemon_id, quest_reward_type, quest_item_id, quest_reward_amount, quest_target
            FROM pokestop WHERE quest_reward_type IS NOT NULL;
        """
        )
        self.quest_data = {"data": quest_data, "date": datetime.now().isoformat()}
        return self.quest_data

    def get_alternative_quest_data(self):
        if self.alternative_quest_data and datetime.now() - datetime.fromisoformat(
            self.alternative_quest_data["date"]
        ) < timedelta(hours=1):
            return self.alternative_quest_data

        alternative_quest_data = self.db.get_data_from_database(
            """
            SELECT name, lat, lon, url, alternative_quest_title, alternative_quest_pokemon_id, alternative_quest_reward_type, alternative_quest_item_id, alternative_quest_reward_amount, alternative_quest_target
            FROM pokestop WHERE alternative_quest_reward_type IS NOT NULL;
        """
        )
        self.alternative_quest_data = {
            "data": alternative_quest_data,
            "date": datetime.now().isoformat(),
        }
        return self.alternative_quest_data

    def find_quest_by_search_keyword(self, search, is_leiria):
        """Find quests by a search keyword."""
        search = search.lower()
        quest_data = self.get_quest_data()["data"]
        alternative_quest_data = self.get_alternative_quest_data()["data"]

        found_quests = self.find_and_process_quest_by_search_keyword(
            search, is_leiria, quest_data
        )
        found_quests.extend(
            self.find_and_process_quest_by_search_keyword(
                search, is_leiria, alternative_quest_data
            )
        )

        return found_quests if found_quests else None

    def find_and_process_quest_by_search_keyword(self, search, is_leiria, quest_data):
        found_quests = []
        mapped_pokemon_ids = self.get_pokemon_id_by_pokemon_name_map(search)
        mapped_item_ids = self.get_item_id_by_item_name_map(search)

        for quest in quest_data:
            if self.is_quest_relevant(
                quest, search, mapped_pokemon_ids, mapped_item_ids, is_leiria
            ):
                quest["quest_slug"] = self.generate_quest_slug_for_image(quest)
                self.add_quest_to_found_quests(found_quests, quest)

        return found_quests

    def is_quest_relevant(
        self, quest, search, mapped_pokemon_ids, mapped_item_ids, is_leiria
    ):
        fields = _quest_fields(quest)
        quest_title = (fields["title"] or "").lower()
        quest_pokemon_id = str(fields["pokemon_id"] or "")
        quest_item_id = str(fields["item_id"] or "")
        quest_target = str(fields["target"] or "")
        quest_reward_type = fields["reward_type"] or ""

        if self.translationfile_data is None:
            logging.warning("Translation data is not available.")
            quest_title_translated = quest_title
        else:
            quest_title_translated = (
                self.translationfile_data["data"]
                .get(quest_title, "")
                .replace("{0}", quest_target)
            )

        if self.is_location_relevant(quest, is_leiria):
            return (
                search in quest_title_translated.lower()
                or quest_pokemon_id in mapped_pokemon_ids
                or quest_item_id in mapped_item_ids
                or (search in "mega energy" and quest_reward_type == 12)
                or (search in "experience" and quest_reward_type == 1)
            )
        return False

    def is_location_relevant(self, quest, is_leiria):
        return (
            ("-8.9" not in str(quest["lon"]))
            if is_leiria
            else ("-8.9" in str(quest["lon"]))
        )

    def add_quest_to_found_quests(self, found_quests, quest):
        fields = _quest_fields(quest)
        quest_title = (fields["title"] or "").lower()
        quest_target = fields["target"]

        quest_target_str = str(quest_target) if quest_target is not None else ""

        if self.translationfile_data is None:
            logging.warning("Translation data is not available.")
            quest_title_translated = quest_title
        else:
            quest_title_translated = (
                self.translationfile_data["data"]
                .get(quest_title, "")
                .replace("{0}", quest_target_str)
            )

        for found_quest in found_quests:
            if found_quest["quest_title"] == quest_title_translated:
                found_quest["quests"].append(quest)
                return
        found_quests.append({"quest_title": quest_title_translated, "quests": [quest]})

    def generate_quest_slug_for_image(self, quest):
        """Generate a quest slug for image URLs."""
        fields = _quest_fields(quest)
        quest_reward_type = fields["reward_type"]
        quest_reward_amount = fields["reward_amount"]
        quest_pokemon_id = fields["pokemon_id"]
        quest_item_id = fields["item_id"]

        if self.masterfile_data is None:
            logging.warning("Masterfile data is not available.")
            return "reward/unknown/0.png"

        if quest_reward_type == 1:  # Experience
            return "reward/experience/0.png"
        elif quest_reward_type == 2:  # Item
            return f"reward/item/{quest_item_id}.png"
        elif quest_reward_type == 3:  # Stardust
            return "reward/stardust/0.png"
        elif quest_reward_type == 4:  # Candies
            return f"reward/candy/{quest_pokemon_id}.png"
        elif quest_reward_type == 7:  # Pokemon
            return f"pokemon/{quest_pokemon_id}.png"
        elif quest_reward_type == 12:  # Mega Energy
            return f"reward/mega_energy/{quest_pokemon_id}.png"
        else:
            try:
                reward_type_name = self.masterfile_data["questRewardTypes"][
                    str(quest_reward_type)
                ]
                return f"reward/{reward_type_name.replace(' ', '_').lower()}/{quest_reward_amount}.png"
            except KeyError:
                logging.warning(
                    f"Unknown quest reward type: {quest_reward_type} - Using default icon"
                )
                return "reward/unknown/0.png"

    def group_pokestops_geographically(self, pokestops, max_per_group=10):
        """Bucket pokestops by ~1km lat/lon grid, then chunk to max_per_group.

        The scan area (Leiria + Marinha Grande) is tight enough that flooring
        coordinates at 2 decimals puts neighbours in the same cell. This gives
        visually coherent groupings without the sklearn dependency.
        """
        if len(pokestops) <= max_per_group:
            return [pokestops]

        buckets: dict[tuple[int, int], list] = {}
        for stop in pokestops:
            key = (int(float(stop["lat"]) * 100), int(float(stop["lon"]) * 100))
            buckets.setdefault(key, []).append(stop)

        result = []
        for group in buckets.values():
            for i in range(0, len(group), max_per_group):
                result.append(group[i : i + max_per_group])
        return result

    def create_quest_embed(
        self, quest_title, pokestops, is_leiria, page=1, total_pages=1
    ):
        location_name = "Leiria" if is_leiria else "Marinha Grande"
        color = discord.Color.blue() if is_leiria else discord.Color.green()
        embed = discord.Embed(
            title=quest_title,
            description=f"Encontrados em {location_name}",
            color=color,
        )

        if pokestops and "quest_slug" in pokestops[0]:
            embed.set_thumbnail(url=f"{self.UI_ICONS_URL}{pokestops[0]['quest_slug']}")

        for stop in pokestops:
            lat, lon = stop["lat"], stop["lon"]
            maps_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
            embed.add_field(
                name=f"📍 {stop['name']}",
                value=f"[Abrir no mapa]({maps_url})",
                inline=False,
            )

        embed.set_footer(text=f"Página {page}/{total_pages}")
        return embed

    def group_pokestops_by_reward(self, found_quests):
        reward_groups = {}
        for quest_group in found_quests:
            quest_title = quest_group["quest_title"]
            for pokestop in quest_group["quests"]:
                if "quest_slug" not in pokestop:
                    continue
                reward_slug = pokestop["quest_slug"]
                if reward_slug not in reward_groups:
                    reward_groups[reward_slug] = {"title": quest_title, "pokestops": []}
                reward_groups[reward_slug]["pokestops"].append(pokestop)

        for group_data in reward_groups.values():
            pokestops = group_data["pokestops"]
            if not pokestops:
                continue
            fields = _quest_fields(pokestops[0])
            reward_type = fields["reward_type"]
            amount = fields["reward_amount"] or ""
            pokemon_id = fields["pokemon_id"] or ""
            item_id = fields["item_id"] or ""

            if reward_type == 2:  # Item
                if amount and item_id and "items" in self.masterfile_data:
                    item_data = self.masterfile_data["items"].get(str(item_id), None)
                    if isinstance(item_data, dict) and "name" in item_data:
                        item_name = item_data["name"]
                    elif isinstance(item_data, str):
                        item_name = item_data
                    else:
                        item_name = ""
                    if item_name:
                        group_data["reward_text"] = f"{amount}x {item_name}"

            elif reward_type == 3:  # Stardust
                if amount:
                    group_data["reward_text"] = f"{amount} Stardust"

            elif reward_type == 4:  # Candies
                if amount and pokemon_id and "pokemon" in self.masterfile_data:
                    pokemon_data = self.masterfile_data["pokemon"].get(
                        str(pokemon_id), None
                    )
                    if isinstance(pokemon_data, dict) and "name" in pokemon_data:
                        group_data["reward_text"] = (
                            f"{amount} {pokemon_data['name']} Candy"
                        )
                    elif isinstance(pokemon_data, str):
                        group_data["reward_text"] = f"{amount} Candy"

            elif reward_type == 7:  # Pokemon
                if pokemon_id and "pokemon" in self.masterfile_data:
                    pokemon_data = self.masterfile_data["pokemon"].get(
                        str(pokemon_id), None
                    )
                    if isinstance(pokemon_data, dict) and "name" in pokemon_data:
                        group_data["reward_text"] = pokemon_data["name"]
                    elif isinstance(pokemon_data, str):
                        group_data["reward_text"] = pokemon_data

            elif reward_type == 12:  # Mega Energy
                if amount and pokemon_id and "pokemon" in self.masterfile_data:
                    pokemon_data = self.masterfile_data["pokemon"].get(
                        str(pokemon_id), None
                    )
                    if isinstance(pokemon_data, dict) and "name" in pokemon_data:
                        pokemon_name = pokemon_data["name"]
                    elif isinstance(pokemon_data, str):
                        pokemon_name = pokemon_data
                    else:
                        pokemon_name = ""
                    if pokemon_name:
                        group_data["reward_text"] = (
                            f"{amount} {pokemon_name} Mega Energy"
                        )

            elif reward_type == 1:  # Experience
                if amount:
                    group_data["reward_text"] = f"{amount} XP"

        return reward_groups

    async def check_tracked(self, channel):
        tracked_quests = self.poliswag.db.get_data_from_database(
            "SELECT target FROM tracked_quest_reward"
        )

        if len(tracked_quests) == 0:
            self.poliswag.utility.log_to_file(
                "Não há quests a serem seguidas atualmente."
            )
            return

        all_found_quests_leiria = []
        all_found_quests_marinha = []

        await channel.send("**RESULTADOS DO SCAN DE QUESTS DE HOJE**")
        for tracked_quest_data in tracked_quests:
            search_keyword = tracked_quest_data["target"]

            found_quests_leiria = (
                self.poliswag.quest_search.find_quest_by_search_keyword(
                    search_keyword, True
                )
            )
            if found_quests_leiria:
                all_found_quests_leiria.extend(found_quests_leiria)

            found_quests_marinha = (
                self.poliswag.quest_search.find_quest_by_search_keyword(
                    search_keyword, False
                )
            )
            if found_quests_marinha:
                all_found_quests_marinha.extend(found_quests_marinha)

        if not all_found_quests_leiria and not all_found_quests_marinha:
            self.poliswag.utility.log_to_file("Nenhuma quest seguida encontrada.")
            return

        from modules.embeds import build_tracked_summary_embeds
        from modules.config import Config

        if all_found_quests_leiria:
            reward_groups_leiria = self.poliswag.quest_search.group_pokestops_by_reward(
                all_found_quests_leiria
            )
            await build_tracked_summary_embeds(
                channel, reward_groups_leiria, "Leiria", Config.UI_ICONS_URL
            )

        if all_found_quests_marinha:
            reward_groups_marinha = (
                self.poliswag.quest_search.group_pokestops_by_reward(
                    all_found_quests_marinha
                )
            )
            await build_tracked_summary_embeds(
                channel, reward_groups_marinha, "Marinha Grande", Config.UI_ICONS_URL
            )
