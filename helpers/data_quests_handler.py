import json, os, requests, datetime

import discord

import helpers.globals as globals
from helpers.notifications import build_quest_location_url
from helpers.utilities import log_error

namesList = ["pokemon", "pokemonuteis"]
discordMessageChannels = {"pokemon": "Spawns Raros", "pokemonuteis": "Spawns Uteis"}
currentDay = datetime.datetime.now().day

def fetch_today_data():
    data = requests.get(globals.BACKEND_ENDPOINT + 'get_quests?fence=None')
    questText = data.text
    quests = json.loads(questText)

    with open(globals.QUESTS_FILE, 'w') as file:
        json.dump(quests, file, indent=4)
    return quests

def write_filter_data(receivedData, add=True):
    if len(receivedData) != 2:
        return False

    originalDiscordChannelName = {"raros": ["pokemon", "pokemonmarinha"], "uteis": ["pokemonuteis", "pokemonuteismarinha"]}
    filterName = {"raros": "spawns-raros", "uteis": "spawns-uteis", "pvp": "spawns-pvp"}

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

    with open(globals.FILTER_FILE, 'w') as file:
        json.dump(jsonPokemonData, file, indent=4)

    return pokemon + (" adicionado a " if add else " removido de ") + filterName[receivedData[1]]

def find_quest(receivedData, leiria):
    if receivedData and (receivedData == "!questleiria" or receivedData == "!questmarinha"):
        return False

    quests = retrieve_sort_quest_data()

    allQuestData = []
    allQuestDataMarinha = []

    for quest in quests:
        reward = build_reward_for_quest(quest)
        try:
            if receivedData and receivedData.lower() in reward.lower() or receivedData.lower() in quest['quest_task'].lower() or receivedData.lower() in quest["name"].lower():
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
            log_error('\nFORCE UPDATE ERROR: %s\n' % str(e))       
            return "Essa procura não me parece ser válida."

    if leiria:
        return allQuestData
    return allQuestDataMarinha

def retrieve_sort_quest_data():
    with open(globals.QUESTS_FILE) as raw_data:
        quests = json.load(raw_data)
    return sorted(quests, key=lambda k: k['quest_task'], reverse=True)

def build_reward_for_quest(quest): 
    if quest['quest_reward_type'] == 'Pokemon':
        reward = quest['pokemon_name']
    elif quest['quest_reward_type'] == 'Item':
        reward = str(quest['item_amount']) + " " + quest['item_type']
    elif quest['quest_reward_type'] == 'Stardust':
        reward = str(quest['item_amount']) + " " + quest['quest_reward_type']
    else:
        reward = str(quest['item_amount']) + " " + quest['pokemon_name'] + " " + quest['item_type']
    return reward

def verify_quest_scan_done():
    with open(globals.QUESTS_FILE) as raw_data:
        jsonPokemonData = json.load(raw_data)
    return len(jsonPokemonData) >= 350
