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

def write_filter_data(receivedData, add=True):
    if len(receivedData) != 2:
        return False

    originalDiscordChannelName = {"raros": ["pokemon", "pokemonmarinha"], "uteis": ["pokemonuteis", "pokemonuteismarinha"]}
    filterName = {"raros": "spawns-raros", "uteis": "spawns-uteis"}

    with open(globals.FILTER_FILE) as raw_data:
        jsonPokemonData = json.load(raw_data)

    try:
        pokemon = receivedData[0].title()
        affectedChannel = originalDiscordChannelName[receivedData[1]]
    except:
        return "Não reconheço esse comando. Aqui tens uma lista para te ajudar." + "\n\n__**COMANDOS IMPLEMENTADOS:**__\n\n> !add POKEMON CANAL\nPara se adicionar um Pokémon a um canal específico `(ex:!add Poliwag uteis)`\n> !remove POKEMON CANAL\nPara que se remova um Pokemon de um canal específico `(ex:!remove Poliwag raros)`\n> !reload\nApós se alterar a lista, podem reiniciar as notificações com as alterações usando `(ex:!reload)`"

    for name in affectedChannel:
        if add and pokemon not in jsonPokemonData['monsters']['filters'][name]['monsters']:
            jsonPokemonData['monsters']['filters'][name]['monsters'].append(pokemon)
        elif not add and pokemon in jsonPokemonData['monsters']['filters'][name]['monsters']:
            jsonPokemonData['monsters']['filters'][name]['monsters'].remove(pokemon)

    os.remove(globals.FILTER_FILE)

    with open(globals.FILTER_FILE, 'w') as file:
        json.dump(jsonPokemonData, file, indent=4)

    return pokemon + (" adicionado a " if add else " removido de ") + filterName[receivedData[1]]

def find_quest(receivedData, leiria):
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

    if leiria:
        return allQuestData

    return allQuestDataMarinha
