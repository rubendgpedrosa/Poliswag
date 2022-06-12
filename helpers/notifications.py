import json

import helpers.globals as globals

namesList = ["pokemon", "pokemonuteis"]
discordMessageChannels = {"pokemon": "Spawns Raros", "pokemonuteis": "Spawns Uteis"}

def load_filter_data():
    global discordMessage

    jsonPokemonData = read_json_data()
    discordMessage = "__** LISTA DE POKÉMON **__\n"

    discordMessage = build_filter_message(discordMessage, jsonPokemonData)

    discordMessage = discordMessage + "\n\n\n__**COMANDOS IMPLEMENTADOS:**__\n\n> !add POKEMON CANAL\nPara se adicionar um Pokémon a um canal específico `(ex:!add Poliwag uteis)`\n> !remove POKEMON CANAL\nPara que se remova um Pokemon de um canal específico `(ex:!remove Poliwag raros)`\n> !reload\nApós se alterar a lista, podem reiniciar as notificações com as alterações usando `(ex:!reload)`"

    return discordMessage

def read_json_data():
    with open(globals.FILTER_FILE) as raw_data:
        jsonPokemonData = json.load(raw_data)
    return jsonPokemonData

def build_filter_message(discordMessage, jsonPokemonData):
    for name in namesList:
        discordMessage = discordMessage + "\n**" + discordMessageChannels[name] + "**\n"
        pokemonNames = []

        for pokemonFilter in jsonPokemonData['monsters']['filters'][name]['monsters']:
            pokemonNames.append(pokemonFilter)

        pokemonNames = sorted(pokemonNames, key=str.lower)
        pokemonNames = "> " + ', '.join(pokemonNames)
        discordMessage = discordMessage + pokemonNames

    return discordMessage