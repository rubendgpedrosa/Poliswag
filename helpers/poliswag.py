import json, requests

import discord

import helpers.constants as constants
from helpers.utilities import log_to_file, build_embed_object_title_description
from helpers.database_connector import get_data_from_database, execute_query_to_database

namesList = ["pokemon", "pokemonuteis"]
discordMessageChannels = {"pokemon": "Spawns Raros", "pokemonuteis": "Spawns Uteis"}

def load_filter_data():
    discordMessage = ""

    jsonPokemonData = read_json_data()

    embed=discord.Embed(title="LISTA DE POKÉMON", color=0x7b83b4)
    for name in namesList:
        discordMessage = build_filter_message(jsonPokemonData, name)
        embed.add_field(name=discordMessageChannels[name], value=discordMessage, inline=False)

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
    log_to_file("New PvP data generated")
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
        if data["score"] >= 80 and len(data["speciesName"].split(" (", 1)[0]) > 1:
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

async def build_commands_message(message):
    embed = discord.Embed(
        title="COMANDOS IMPLEMENTADOS",
        color=0x7b83b4
    )
    
    embed.add_field(name="!alertas", value="Lista de Pokémon nas Notificações", inline=False)
    embed.add_field(name="!add POKEMON CANAL", value="Adicionar pokémon a canal de notificações", inline=False)
    embed.add_field(name="!remove POKEMON CANAL", value="Remover pokémon de canal de notificações", inline=False)
    embed.add_field(name="!reload", value="Submeter alterações nas notificações", inline=False)
    embed.add_field(name="!questclear", value="Limpa lista de quests", inline=False)
    embed.add_field(name="!scan", value="Força novo scan de quests", inline=False)
    embed.add_field(name="!lures", value="Lista as contas com lures disponíveis", inline=False)
    embed.add_field(name="!uselure USERNAME NUMBER", value="NUMBER negativo -> remove NUMBER lures.\nNUMBER positivo -> adiciona NUMBER lures.", inline=False)
    embed.add_field(name="!upcoming", value="Lista rescan de quests agendados", inline=False)
    embed.add_field(name="!logs", value="JÁ VISTE OS LOGS????", inline=False)
    
    await message.channel.send(embed=embed)

def build_message_accounts_available():
    availableAccounts = get_available_pogo_accounts()
    return append_extra_data_to_accounts(availableAccounts)
    
def get_available_pogo_accounts():
    #storedAvailableAccounts = get_data_from_database(f"SELECT username FROM settings_pogoauth WHERE device_id IS NULL AND last_burn < DATE_ADD(NOW(), INTERVAL 3 DAY) AND last_burn > ( SELECT MIN(last_burn) FROM settings_pogoauth) + INTERVAL 2 DAY;")
    storedAvailableAccounts = get_data_from_database(f"SELECT username FROM settings_pogoauth WHERE device_id IS NULL AND account_id < 43;")

    availableAccounts = []
    for storedAvailableAccount in storedAvailableAccounts:
        availableAccounts.append(storedAvailableAccount["data"][0])

    return availableAccounts


def build_database_variable_from_accounts_list(availableAccounts):
    return ', '.join(['"{}"'.format(s) for s in availableAccounts])

def append_extra_data_to_accounts(availableAccounts):
    queryVariablePreparedUsernames = build_database_variable_from_accounts_list(availableAccounts)
    
    storedAvailableAccounts = get_data_from_database(f"SELECT username, nb_lures FROM account_lure WHERE username IN ({queryVariablePreparedUsernames}) ORDER BY nb_lures ASC;", "poliswag")
    existingUsernames = [account["data"][0] for account in storedAvailableAccounts]  # Extract usernames

    availableAccountsList = []
    if len(availableAccounts) > 0:
        for account_data in storedAvailableAccounts:
            # stop iteration once availableAccountsList >= 5
            if len(availableAccountsList) >= 5:
                break
            username = account_data["data"][0]
            nb_lures = account_data["data"][1]
            if nb_lures > 0:
                availableAccountsList.append({"username": username, "nb_lures": nb_lures})

        for username in availableAccounts:
            if username not in existingUsernames:
                print(f"Account {username} not found in database")
                execute_query_to_database(f"INSERT INTO account_lure (username) VALUES ('{username}');", "poliswag")
                availableAccountsList.append({"username": username, "nb_lures": 12})

    return availableAccountsList

async def notify_accounts_available_message(message):
    availableAccounts = build_message_accounts_available()
    
    if (len(availableAccounts) == 0):
        return

    tempString = ""
    for account in availableAccounts:
        tempString = tempString + f"{account['username']} - {account['nb_lures']} lures\n"

    await message.channel.send(embed=build_embed_object_title_description(
            "LISTA DE CONTAS DISPONÍVEIS", 
            tempString
        )
    )

async def decrement_and_notify_lure_count_by_username(message):
    receivedData = message.content.replace("!uselure ","")
    receivedData = receivedData.split(" ")
    
    username = receivedData[0]
    nb_lures = int(receivedData[1])
    

    action = "removida" if nb_lures < 0 else "adicionada"
    plural = "" if abs(nb_lures) == 1 else "s"
    
    execute_query_to_database(
        f"UPDATE account_lure SET nb_lures = GREATEST(nb_lures + {nb_lures}, 0) WHERE username = '{username}';",
        "poliswag"
    )
    
    log_message = f"{abs(nb_lures)} {'lure' if abs(nb_lures) == 1 else 'lures'} {action}{plural} à conta {username} por {message.author.name}"
    log_to_file(log_message)
    
    await message.channel.send(embed=build_embed_object_title_description(
            f"{abs(nb_lures)} lure{plural} {action}{plural} da conta {username} por {message.author.name}"
        )
    )
