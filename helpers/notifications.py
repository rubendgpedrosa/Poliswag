import json
from urllib.request import Request, urlopen

import discord

import helpers.globals as globals

namesList = ["pokemon", "pokemonuteis"]
discordMessageChannels = {"pokemon": "Spawns Raros", "pokemonuteis": "Spawns Uteis"}

# URLS
great_league_endpoint = "https://raw.githubusercontent.com/pvpoke/pvpoke/master/src/data/rankings/gobattleleague/overall/rankings-1500.json"
ultra_league_endpoint = "https://raw.githubusercontent.com/pvpoke/pvpoke/master/src/data/rankings/gobattleleague/overall/rankings-2500.json"

def load_filter_data(displayCommands = True):
    discordMessage = ""

    jsonPokemonData = read_json_data()

    #discordMessage= embed.add_field(name="Raros", value="asdasdasdasdasdasdasd", inline=False)
    embed=discord.Embed(title="LISTA DE POKÉMON", color=0x7b83b4)
    for name in namesList:
        discordMessage = build_filter_message(jsonPokemonData, name)
        embed.add_field(name=discordMessageChannels[name], value=discordMessage, inline=True)
    if displayCommands:
        embed.set_footer(text="COMANDOS IMPLEMENTADOS:  !add POKEMON CANAL, !remove POKEMON CANAL, !reload")

    return embed

def read_json_data():
    with open(globals.FILTER_FILE) as raw_data:
        jsonPokemonData = json.load(raw_data)
    return jsonPokemonData

def build_filter_message(jsonPokemonData, name):
    pokemonNames = []

    for pokemonFilter in jsonPokemonData['monsters']['filters'][name]['monsters']:
        pokemonNames.append(pokemonFilter)
    pokemonNames = sorted(pokemonNames, key=str.lower)
    pokemonNames = "> " + ', '.join(pokemonNames)
    
    return pokemonNames

def build_quest_message(data):
    return "[" + data['name'] + "](" + build_quest_location_url(data["latitude"], data["longitude"]) + ")"

def build_quest_location_url(latitude, longitude):
    coordinatesUrl = "https://www.google.com/maps/search/?api=1&query=" + str(latitude) + "," + str(longitude)

    return coordinatesUrl

def fetch_new_pvp_data():
    request = Request(great_league_endpoint, headers={'User-Agent': 'Mozilla/5.0'})
    jsonData = urlopen(request).read()

