import json, requests

import discord

import helpers.constants as constants

namesList = ["pokemon", "pokemonuteis"]
discordMessageChannels = {"pokemon": "Spawns Raros", "pokemonuteis": "Spawns Uteis"}

def load_filter_data(displayCommands = True):
    discordMessage = ""

    jsonPokemonData = read_json_data()

    embed=discord.Embed(title="LISTA DE POKÉMON", color=0x7b83b4)
    for name in namesList:
        discordMessage = build_filter_message(jsonPokemonData, name)
        embed.add_field(name=discordMessageChannels[name], value=discordMessage, inline=False)
    if displayCommands:
        embed.set_footer(text="COMANDOS IMPLEMENTADOS:  !add POKEMON CANAL, !remove POKEMON CANAL, !reload")

    return embed

def read_json_data():
    with open(constants.FILTER_FILE) as raw_data:
        jsonPokemonData = json.load(raw_data)
    return jsonPokemonData

def build_filter_message(jsonPokemonData, name):
    pokemonNames = []

    for pokemonFilter in jsonPokemonData['monsters']['filters'][name]['monsters']:
        pokemonNames.append(pokemonFilter)
    pokemonNames = sorted(pokemonNames, key=str.lower)
    pokemonNames = ', '.join(pokemonNames)
    
    return pokemonNames

def fetch_new_pvp_data():
    greatLeagueData = fetch_data_from_endpoint(1500)
    ultraLeagueData = fetch_data_from_endpoint(2500)

    greatLeagueData = prepare_fetched_json_object(greatLeagueData)
    ultraLeagueData = prepare_fetched_json_object(ultraLeagueData)

    with open(constants.FILTER_FILE) as raw_data:
        jsonFiltersPokemonData = json.load(raw_data)

    # Clears existing PvP content
    jsonFiltersPokemonData['monsters']['filters']["pvp_great"]['monsters'] = []
    jsonFiltersPokemonData['monsters']['filters']["pvp_great_marinha"]['monsters'] = []
    jsonFiltersPokemonData['monsters']['filters']["pvp_ultra"]['monsters'] = []
    jsonFiltersPokemonData['monsters']['filters']["pvp_ultra_marinha"]['monsters'] = []

    build_notification_data_for_pvp(greatLeagueData, ultraLeagueData, jsonFiltersPokemonData)

    with open(constants.FILTER_FILE, 'w') as file:
        json.dump(jsonFiltersPokemonData, file, indent=4)
    return

def fetch_data_from_endpoint(combat_power):
    request = requests.get(f"https://raw.githubusercontent.com/pvpoke/pvpoke/master/src/data/rankings/all/overall/rankings-{combat_power}.json")
    # The .json() method automatically parses the response into JSON.
    return request.json()
    
def prepare_fetched_json_object(jsonData):
    # remove duplicates from list of dictionaries from jsonData
    jsonDataNoDuplicates = [k for j, k in enumerate(jsonData) if k not in jsonData[j + 1:]]
    jsonData = sorted(jsonDataNoDuplicates, key=lambda d: d["score"], reverse=True)
    listPokemonForNotifications = []
    for data in jsonData:
        if data["score"] >= 85 and len(data["speciesName"].split(" (", 1)[0]) > 1:
            listPokemonForNotifications.append(data["speciesName"].split(" (", 1)[0])
    return listPokemonForNotifications

def build_notification_data_for_pvp(greatLeagueData, ultraLeagueData, jsonFiltersPokemonData):
    # https://pokemon.gameinfo.io/ json provider
    # Matrix holding family trees
    with open(constants.POKEMON_LIST_FILE) as raw_data:
        jsonPokemonList = json.load(raw_data)

    for jsonPokemonListX in jsonPokemonList:
        familyList = []
        for jsonPokemonListY in jsonPokemonListX:
            # Gets name in the dict by transforming first key in into a list then getting first element (name)
            pokemonName = list(jsonPokemonListY.values())[0][1]
            familyList.append(pokemonName)
            if pokemonName in greatLeagueData and pokemonName not in jsonFiltersPokemonData['monsters']['filters']["pvp_great"]['monsters']:
                for nameToInsert in familyList:
                    jsonFiltersPokemonData['monsters']['filters']["pvp_great"]['monsters'].append(nameToInsert)
                    jsonFiltersPokemonData['monsters']['filters']["pvp_great_marinha"]['monsters'].append(nameToInsert)
            if pokemonName in ultraLeagueData and pokemonName not in jsonFiltersPokemonData['monsters']['filters']["pvp_ultra"]['monsters']:
                    for nameToInsert in familyList:
                        jsonFiltersPokemonData['monsters']['filters']["pvp_ultra"]['monsters'].append(nameToInsert)
                        jsonFiltersPokemonData['monsters']['filters']["pvp_ultra_marinha"]['monsters'].append(nameToInsert)