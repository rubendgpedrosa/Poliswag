import json, os, requests

import helpers.globals as globals
from helpers.notifications import build_quest_location_url

namesList = ["pokemon", "pokemonuteis"]
discordMessageChannels = {"pokemon": "Spawns Raros", "pokemonuteis": "Spawns Uteis"}

def fetch_today_data():
    data = requests.get(globals.BACKEND_ENDPOINT + 'get_quests?fence=None')
    questText = data.text
    quests = json.loads(questText)

    os.remove(globals.QUESTS_FILE)

    with open(globals.QUESTS_FILE, 'w') as file:
        json.dump(quests, file, indent=4)

def find_quest(receivedData, local):
    if receivedData and (receivedData == "!questleiria" or receivedData == "!questmarinha"):
        return False

    with open(globals.QUESTS_FILE) as raw_data:
        quests = json.load(raw_data)
    quests = sorted(quests, key=lambda k: k['quest_task'], reverse=True)

    allQuestData = []
    allQuestDataMarinha = []

    for quest in quests:
        if quest['quest_reward_type'] == 'Pokemon':
            reward = quest['pokemon_name']
        elif quest['quest_reward_type'] == 'Item':
            reward = str(quest['item_amount']) + " " + quest['item_type']
        elif quest['quest_reward_type'] == 'Stardust':
            reward =  str(quest['item_amount']) + " " + quest['quest_reward_type']
        else:
            reward =  str(quest['item_amount']) + " " + quest['pokemon_name'] + " " + quest['item_type']

        try:
            if receivedData.lower() in reward.lower() or receivedData.lower() in quest['quest_task'].lower() or receivedData.lower() in quest["name"].lower():
                if "-8.9" not in str(quest['longitude']):
                    allQuestData.append({
                        "name": "[Leiria] " + quest["name"],
                        "map": build_quest_location_url(quest["latitude"], quest["longitude"]),
                        "quest": quest['quest_task'] + " - " + reward,
                        "image": quest["url"]
                    })
                else:
                    allQuestDataMarinha.append({
                        "name": "[Marinha Grande] " + quest["name"],
                        "map": build_quest_location_url(quest["latitude"], quest["longitude"]),
                        "quest": quest['quest_task'] + " - " + reward,
                        "image": quest["url"]
                    })
        except Exception as e:
            print(e)
            return "Essa procura não me parece ser válida."

    if local == 1:
        return allQuestData

    return allQuestDataMarinha