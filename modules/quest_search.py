import os
import requests
from datetime import datetime, timedelta
import json
from modules.database_connector import DatabaseConnector

class QuestSearch:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.db = DatabaseConnector(os.environ.get("DB_SCANNER"))
        self.POKEMON_NAME_FILE = os.environ.get("POKEMON_NAME_FILE")
        self.ITEM_NAME_FILE = os.environ.get("ITEM_NAME_FILE")

        self.masterfile_data = None
        self.translationfile_data = None
        self.quest_data = None
        self.alternative_quest_data = None

        self.get_translationfile_data()
        self.get_masterfile_data()
        self.generate_pokemon_item_name_map()

    def get_quest_data(self):
        if self.quest_data is not None and datetime.now() - datetime.fromisoformat(self.quest_data["date"]) < timedelta(hours=1):
            return self.quest_data

        quest_data = self.db.get_data_from_database(
            """
            SELECT name,
            lat,
            lon,
            url,
            quest_title,
            quest_pokemon_id,
            quest_reward_type,
            quest_item_id,
            quest_reward_amount,
            quest_target
            FROM pokestop WHERE quest_reward_type IS NOT NULL;
            """
        )
    
        quest_data = {"data": quest_data, "date": datetime.now().isoformat()}
        self.quest_data = quest_data
        return quest_data
    
    def get_alternative_quest_data(self):
        if self.alternative_quest_data is not None and datetime.now() - datetime.fromisoformat(self.alternative_quest_data["date"]) < timedelta(hours=1):
            return self.alternative_quest_data

        alternative_quest_data = self.db.get_data_from_database(
            """
            SELECT name,
            lat,
            lon,
            url,
            alternative_quest_title,
            alternative_quest_pokemon_id,
            alternative_quest_reward_type,
            alternative_quest_item_id,
            alternative_quest_reward_amount,
            alternative_quest_target
            FROM pokestop WHERE alternative_quest_reward_type IS NOT NULL;
            """
        )
    
        alternative_quest_data = {"data": alternative_quest_data, "date": datetime.now().isoformat()}
        self.alternative_quest_data = alternative_quest_data
        return alternative_quest_data

    def get_masterfile_data(self):
        if self.masterfile_data is not None and datetime.now() - datetime.fromisoformat(self.masterfile_data["date"]) < timedelta(hours=24):
            return self.masterfile_data

        masterfile_json = requests.get(os.environ.get("MASTERFILE_URL"))
        if masterfile_json.status_code == 200:
            masterfile_json = masterfile_json.json()
            masterfile_json = {key: value for key, value in masterfile_json.items() if key in ["items", "questRewardTypes", "pokemon"]}
            masterfile_json["date"] = datetime.now().isoformat()
            self.masterfile_data = masterfile_json
            return masterfile_json

    def get_translationfile_data(self):
        if self.translationfile_data is not None and datetime.now() - datetime.fromisoformat(self.translationfile_data["date"]) < timedelta(hours=24):
            return self.translationfile_data

        translationfile_json = requests.get(os.environ.get("TRANSLATIONFILE_URL"))
        if translationfile_json.status_code == 200:
            translationfile_json = translationfile_json.json()
            translation_data = translationfile_json.get("data", [])
            translated_dict = {}
            # This is done as the returned json from the translationfile is a list of key-value pairs separated by commas: "key1", "value1", "key2", "value2"...
            for i in range(0, len(translation_data), 2):
                if i + 1 < len(translation_data):  # Ensure there's both key and value
                    key = translation_data[i].strip('"')  # Remove quotes
                    value = translation_data[i + 1].strip('"')  # Remove quotes
                    translated_dict[key] = value
            translationfile_json["data"] = translated_dict
            translationfile_json["date"] = datetime.now().isoformat()
            self.translationfile_data = translationfile_json
            return translationfile_json

    def get_pokemon_id_by_pokemon_name_map(self, search):
        possible_pokemon_id_list = []
        with open(self.POKEMON_NAME_FILE, "r") as file:
            pokemon_name_map = json.load(file)
            for pokemon_id, pokemon_name in pokemon_name_map.items():
                if search.lower() in pokemon_name.lower():
                    possible_pokemon_id_list.append(pokemon_id)
        return possible_pokemon_id_list

    def get_item_id_by_item_name_map(self, search):
        possible_item_id_list = []
        with open(self.ITEM_NAME_FILE, "r") as file:
            item_name_map = json.load(file)
            for item_id, item_name in item_name_map.items():
                if search.lower() in item_name.lower():
                    possible_item_id_list.append(item_id)
        return possible_item_id_list

    def generate_pokemon_item_name_map(self):
        if not self.masterfile_data or "pokemon" not in self.masterfile_data:
            print("Error: Masterfile data or 'pokemon' key is missing.")
            return

        pokemon_data = self.masterfile_data["pokemon"]
        pokemon_name_map = {pokemon_id: details["name"] for pokemon_id, details in pokemon_data.items()}

        item_data = self.masterfile_data["items"]
        item_name_map = {item_id: details for item_id, details in item_data.items()}

        with open(self.POKEMON_NAME_FILE, "w") as file:
            json.dump(pokemon_name_map, file, indent=4)
        with open(self.ITEM_NAME_FILE, "w") as file:
            json.dump(item_name_map, file, indent=4)

    def find_quest_by_search_keyword(self, search, is_leiria):
        search = search.lower()
        quest_data = self.get_quest_data()["data"]
        alternative_quest_data = self.get_alternative_quest_data()["data"]

        found_quests = []
        found_quests.extend(self.find_and_process_quest_by_search_keyword(search, is_leiria, quest_data))
        found_quests.extend(self.find_and_process_quest_by_search_keyword(search, is_leiria, alternative_quest_data))

        if found_quests == []:
            return None

        return found_quests

    def find_and_process_quest_by_search_keyword(self, search, is_leiria, quest_data):
        found_quests = []

        mapped_pokemon_ids = self.get_pokemon_id_by_pokemon_name_map(search) # Get possible pokemon ids from the search string
        mapped_item_ids = self.get_item_id_by_item_name_map(search) # Get possible item ids from the search string

        if_dynamic_condition = lambda quest: ("-8.9" not in str(quest['lon'])) if is_leiria else ("-8.9" in str(quest['lon']))

        for quest in quest_data:
            # Determine the appropriate field names based on the context
            title_field = "alternative_quest_title" if "alternative_quest_title" in quest else "quest_title"
            pokemon_id_field = "alternative_quest_pokemon_id" if "alternative_quest_pokemon_id" in quest else "quest_pokemon_id"
            item_id_field = "alternative_quest_item_id" if "alternative_quest_item_id" in quest else "quest_item_id"
            target_field = "alternative_quest_target" if "alternative_quest_target" in quest else "quest_target"
            reward_type_field = "alternative_quest_reward_type" if "alternative_quest_reward_type" in quest else "quest_reward_type"
            reward_amount_field = "alternative_quest_reward_amount" if "alternative_quest_reward_amount" in quest else "quest_reward_amount"

            # Retrieve the values from the quest data using the determined field names
            quest_title = quest.get(title_field, "").lower()
            quest_pokemon_id = str(quest.get(pokemon_id_field, ""))
            quest_item_id = str(quest.get(item_id_field, ""))
            quest_target = str(quest.get(target_field, ""))
            quest_reward_type = quest.get(reward_type_field, "")
            quest_reward_amount = quest.get(reward_amount_field, "")

            # Translate the quest title
            quest_title_translated = self.translationfile_data["data"].get(quest_title, "").replace("{0}", quest_target)

            if if_dynamic_condition(quest):
                if search in quest_title_translated.lower() or quest_pokemon_id in mapped_pokemon_ids or quest_item_id in mapped_item_ids or (search in "mega energy" and quest_reward_type == 12) or (search in "experience" and quest_reward_type == 1):
                    quest["quest_slug"] = self.generate_quest_slug_for_image(quest_reward_type, quest_pokemon_id, quest_reward_amount, quest_item_id)
                    quest_title_found = False
                    for found_quest in found_quests:
                        if found_quest["quest_title"] == quest_title_translated:
                            found_quest["quests"].append(quest)
                            quest_title_found = True
                            break
                    if not quest_title_found:
                        found_quests.append({"quest_title": quest_title_translated, "quests": [quest]})  # Append [quest] instead of quest

        return found_quests

    def generate_quest_slug_for_image(self, quest_reward_type, quest_pokemon_id = None, quest_reward_amount = None, quest_item_id = None):
        quest_reward_masterfile_string = self.masterfile_data["questRewardTypes"][str(quest_reward_type)]

        if quest_reward_type == 1:  # Experience
            quest_reward_masterfile_string = "reward/experience/0.png"
        elif quest_reward_type == 2:  # item
            quest_reward_masterfile_string = "reward/item/" + str(quest_item_id) + ".png"
        elif quest_reward_type == 3:  # stardust
            quest_reward_masterfile_string = "reward/" + quest_reward_masterfile_string.replace(" ", "_").lower() + "/0.png"
        elif quest_reward_type == 7:  # Pokemon
            quest_reward_masterfile_string = "pokemon/" + str(quest_pokemon_id) + ".png"
        elif quest_reward_type == 12:  # Mega Energy
            quest_reward_masterfile_string = "reward/" + quest_reward_masterfile_string.replace(" ", "_").lower() + "/" + str(quest_pokemon_id) + ".png"
        else:
            quest_reward_masterfile_string = "reward/" + quest_reward_masterfile_string.replace(" ", "_").lower() + "/" + str(quest_reward_amount) + ".png"

        return quest_reward_masterfile_string
