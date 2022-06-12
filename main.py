#!/usr/bin/python\
import datetime, json, os, requests, random, sys
from os.path import exists

import discord
from bs4 import BeautifulSoup
from discord.ext import tasks
from dotenv import load_dotenv

from helpers.environment import prepare_environment

if (len(sys.argv) != 2):
    print("Invalid number of arguments, usage: python3 main.py (dev|prod)")
    quit()

load_dotenv(prepare_environment(sys.argv[1]))
DISCORD_API_KEY = os.getenv('DISCORD_API_KEY')

BACKEND_ENDPOINT = os.getenv('BACKEND_ENDPOINT')
WEBSITE_URL = os.getenv('WEBSITE_URL')

## VARIABLES FOR CACHING SYSTEM REGARDING SPAM
messagesReceived = []
messagesToDelete = {}
messagesFlaggedToBeDeleted = []
authorToMute = ""

## QUESTS
namesList = ["pokemon", "pokemonuteis"]
discordMessageChannels = {"pokemon": "Spawns Raros", "pokemonuteis": "Spawns Uteis"}
client = discord.Client()
allowedUsers = ["98846248865398784", "308000681271492610", "313738342904627200", "237306098976161793"]
fileFilter = "/root/MAD-docker/PokeAlarm/filters.json"
fileQuest = "/root/MAD-docker/PokeAlarm/quest_data.json"
fileScanned = '/root/poliswag/scanned.pogoleiria'
fileVersion = '/root/poliswag/version.pogoleiria'
fileClearQuests = '/root/poliswag/clearquests.pogoleiria'
logFile = '/root/poliswag/log.txt'
pogoleiriaurl = WEBSITE_URL + "static/images/header/pogoleiria_rounded.png"
modChannel = 609087040939622400
questChannel = 897240891759149087
convivioChannel = 329321552241754115
mapStatsChannel = 934146276377903124
myId = 98846248865398784
event_channel = 946803671881089095
muted_role = ""

comandoQuestsTitle = "__COMANDOS IMPLEMENTADOS:__"
comandoQuestsBody = "> !questleiria/questmarinha POKÉSTOP/QUEST/RECOMPENSA\nDevolve uma lista de resultados onde a pokéstop, quest ou recompensa correspondam ao texto inserido\n`(ex:!questmarinha startdust | !questleiria tribunal)`"

def load_filter_data():
    global discordMessage

    jsonPokemonData = read_json_data()
    discordMessage = "__** LISTA DE POKÉMON **__\n"

    discordMessage = build_filter_message(discordMessage, jsonPokemonData)

    discordMessage = discordMessage + "\n\n\n__**COMANDOS IMPLEMENTADOS:**__\n\n> !add POKEMON CANAL\nPara se adicionar um Pokémon a um canal específico `(ex:!add Poliwag uteis)`\n> !remove POKEMON CANAL\nPara que se remova um Pokemon de um canal específico `(ex:!remove Poliwag raros)`\n> !reload\nApós se alterar a lista, podem reiniciar as notificações com as alterações usando `(ex:!reload)`"

    return discordMessage

def read_json_data():
    with open(fileFilter) as raw_data:
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

def build_quest_message(data):
    return "[" + data['name'] + "](" + build_quest_location_url(data["latitude"], data["longitude"]) + ")"

def build_quest_location_url(latitude, longitude):
    coordinatesUrl = "https://www.google.com/maps/search/?api=1&query=" + str(latitude) + "," + str(longitude)

    return coordinatesUrl

def fetch_events():
    html = requests.get('https://www.leekduck.com/events/')
    processedHTML = BeautifulSoup(html.text, 'html.parser')
    targetDivs = processedHTML.find_all("div", {"class": "event-text"})
    
    eventMessages = "__**EVENTOS ATIVOS E FUTUROS EVENTOS**__\n\n"
    for targetDiv in targetDivs:
        eventMessages = eventMessages + "**" + targetDiv.find("h2", {"class": ""}).text + "**" + "\n"
        eventMessages = eventMessages + "> " + targetDiv.find("p", {"class": ""}).text + "\n"

    return eventMessages

def write_filter_data(receivedData, add=True):
    if len(receivedData) != 2:
        return False

    originalDiscordChannelName = {"raros": ["pokemon", "pokemonmarinha"], "uteis": ["pokemonuteis", "pokemonuteismarinha"]}
    filterName = {"raros": "spawns-raros", "uteis": "spawns-uteis"}

    with open(fileFilter) as raw_data:
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

    os.remove(fileFilter)

    with open(fileFilter, 'w') as file:
        json.dump(jsonPokemonData, file, indent=4)

    return pokemon + (" adicionado a " if add else " removido de ") + filterName[receivedData[1]]

def find_quest(receivedData, local):
    if receivedData and (receivedData == "!questleiria" or receivedData == "!questmarinha"):
        return False

    with open(fileQuest) as raw_data:
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

def fetch_today_data():
    data = requests.get(BACKEND_ENDPOINT + 'get_quests?fence=None')
    questText = data.text
    quests = json.loads(questText)

    os.remove(fileQuest)

    with open(fileQuest, 'w') as file:
        json.dump(quests, file, indent=4)

def get_version():
    #with open('savedVersion.txt', 'r') as data:
    with open('/root/version/savedVersion.txt', 'r') as file:
        data = file.read().rstrip()
    return data
    
def handleMessageReceived(message):
    if len(messagesReceived) > 50:
        messagesReceived.pop(0)
    messagesReceived.append({ "authorId": message.author.id,"messageId": message.id,"message": message.content, "timestamp": datetime.now().timestamp(), "channelId": message.channel.id })

def checkIfDuplicate(message):
    counter = 0
    for msgRec in messagesReceived:
        if msgRec["message"] == message.content and message.author.id == msgRec["authorId"] and msgRec["timestamp"] - datetime.now().timestamp() < 5:
            counter += 1
    return counter > 2

def clearMessages(authorId):
    i = 0
    while i < len(messagesReceived):
        if messagesReceived[i]["authorId"] == authorId:
            messagesReceived.pop(i)

@tasks.loop(minutes=1)
async def prepare_daily_quest_message_task():
    file_exists_scanned = exists(fileScanned)
    file_exists_version = exists(fileVersion)
    file_exists_clear = exists(fileClearQuests)

    if file_exists_scanned:
        color = random.randint(0, 16777215)
        try:
            channel = client.get_channel(questChannel)
            try:
                fetch_today_data()
                embed = discord.Embed(title="Scan de quests finalizado!", description="Todas as informações relacionadas com as quests foram recolhidas", color=color)
                embed.set_footer(text="Esta informação só é válida até ao final do dia")
                await channel.send(embed=embed)
                os.remove(fileScanned)
            except OSError as e:
                f = open(logFile, 'w')
                f.write('FETCHING QUESTS ERROR: %s' % str(e))
                f.close()
                os.system("ps -ef | grep '/poliswag/main.py' | grep -v grep | awk '{print $2}' | xargs -r kill -9")
        except OSError as e:
            f = open(logFile, 'w')
            f.write('FETCHING QUESTS ERROR: %s' % str(e))
            f.close()
            os.system("ps -ef | grep '/poliswag/main.py' | grep -v grep | awk '{print $2}' | xargs -r kill -9")
    
    if file_exists_version:
        color = random.randint(0, 16777215)
        try:
            channel = client.get_channel(convivioChannel)
            embed = discord.Embed(title="PAAAAUUUUUUUU!!! FORCE UPDATE!", description="Nova versão: " + get_version(), color=color)
            await channel.send(embed=embed)
            os.remove(fileVersion)
        except OSError as e:
            f = open(logFile, 'w')
            f.write('\nFORCE UPDATE ERROR: %s\n' % str(e))
            f.close()

    if file_exists_clear:
        color = random.randint(0, 16777215)
        try:
            channel = client.get_channel(questChannel)
            embed = discord.Embed(title="Quests de hoje expiraram!", description="Lista de quests do dia anterior foi eliminada e a recolha das novas quests será feita durante a noite.", color=color)
            await channel.send(embed=embed)
            os.remove(fileClearQuests)
        except OSError as e:
            f = open(logFile, 'w')
            f.write('\nErro a a limpar quests: %s\n' % str(e))
            f.close()

@client.event
async def on_ready():
    prepare_daily_quest_message_task.start()

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    muted_role = discord.utils.get(message.guild.roles, name="Muted")

    color = random.randint(0, 16777215)

    if message.content in messagesFlaggedToBeDeleted and str(message.author.id) not in allowedUsers:
        try:
            channel = client.get_channel(modChannel)
            user = message.author
            await user.add_roles(muted_role, reason="Desiludiste o Lord Poliswag com o teu spam", atomic=True)
            embed = discord.Embed(title=str(user) + " levou mute por spam!", description=message.content, color=color)
            embed.set_thumbnail(url=user.avatar_url)
            embed.add_field(name="DiscordId", value=user.id, inline=False)
            await channel.send(embed=embed)
            await message.delete()
        except:
            print('Failed at initial message')
    elif len(message.content) > 8 and str(message.author.id) not in allowedUsers and message.channel.id != questChannel:
        handleMessageReceived(message)

    if message.channel.id == mapStatsChannel:
        channel = client.get_channel(mapStatsChannel)
        async for msg in channel.history(limit=200):
            if message != msg:
                await msg.delete()

    if message.channel.id == event_channel:
        if message.content.lower() == 'gold':
            event_role = discord.utils.get(message.guild.roles, name="Gold")
        elif message.content.lower() == 'silver':
            event_role = discord.utils.get(message.guild.roles, name="Silver")
        elif str(message.author.id) not in allowedUsers:
            await message.delete()
        await message.author.add_roles(event_role, atomic=True)
        await message.add_reaction("✅")

    if message.channel.id != modChannel and str(message.author.id) not in allowedUsers:
        toDelete = checkIfDuplicate(message)
        if toDelete and message.content not in messagesFlaggedToBeDeleted:
            messagesFlaggedToBeDeleted.append(message.content)

            for msgDel in messagesReceived:
                if msgDel["message"] in messagesFlaggedToBeDeleted:
                    try:
                        channelMsg = client.get_channel(msgDel["channelId"])
                        msgd = await channelMsg.fetch_message(msgDel["messageId"])
                        await msgd.delete()
                    except:
                        print('Failed deleting for loop')
            try:
                channel = client.get_channel(modChannel)
                user = message.author
                await user.add_roles(muted_role, reason="Desiludiste o Lord Poliswag com o teu spam", atomic=True)
                embed = discord.Embed(title=str(user) + " levou mute por spam!", description=message.content, color=color)
                embed.set_thumbnail(url=user.avatar_url)
                embed.add_field(name="DiscordId", value=user.id, inline=False)
                await channel.send(embed=embed)
            except:
                print('Already deleted')
            # clearMessages(msgDel["authorId"])

    if message.channel.id == modChannel:
        if str(message.author.id) in allowedUsers:
            if message.content.startswith('!m') and message.author.id == myId:
                receivedData = message.content.replace("!m ","")
                channel = client.get_channel(convivioChannel)
                await channel.send(receivedData)

            if message.content.startswith('!filter'):
                await message.channel.send(load_filter_data())

            if message.content.startswith('!add'):
                receivedData = message.content.replace("!add ","")
                receivedData = receivedData.split(" ", 1)
                returnedData = write_filter_data(receivedData)
                if returnedData == False:
                    await message.channel.send("Woops, parece que te enganaste migo.")
                    return
                await message.channel.send(returnedData)

            if message.content.startswith('!remove'):
                receivedData = message.content.replace("!remove ","")
                receivedData = receivedData.split(" ", 1)
                returnedData = write_filter_data(receivedData, False)
                if returnedData == False:
                    await message.channel.send("Woops, parece que te enganaste migo.")
                    return
                await message.channel.send(returnedData)

            if message.content.startswith('!reload'):
                os.system('docker restart pokemon_alarm')
                embed = discord.Embed(title="A lista de pokémon das notificações foi alterada", description="Utiliza !filter para ver quais são os novos filtros", color=color)
                await message.channel.send(embed=embed)
                channel = client.get_channel(convivioChannel)
                await channel.send(embed=embed)

            if message.content.startswith('!scan'):
                embed = discord.Embed(title="Rescan de pokestops inicializado", color=color)
                await message.channel.send(embed=embed)
                channel = client.get_channel(questChannel)
                await channel.send(embed=embed)
                os.system("bash /root/MAD-docker/scan.sh")


            if message.content.startswith('!eventos'):
                events = fetch_events()
                await message.channel.send(events)

    if message.channel.id == questChannel:
        if message.content.startswith('!comandos'):
            embed = discord.Embed(title=comandoQuestsTitle, description=comandoQuestsBody, color=color)
            await message.channel.send(embed=embed)

        if message.content.startswith('!questleiria'):
            receivedData = message.content.replace("!questleiria ","")
            returnedData = find_quest(receivedData, 1)
            if returnedData == False:
                try:
                    await message.delete()
                    return
                except:
                    print('woops')

            if len(returnedData) > 0 and len(returnedData) < 25:
                try:
                    for data in returnedData:
                        # Initiate the discord main message
                        embed = discord.Embed(title=data["name"], url=data["map"], description=data["quest"], color=color)
                        # Set pokestop thumbnail image
                        embed.set_thumbnail(url=data["image"])
                        # Tag the author + add the search query
                        embed.add_field(name=message.author, value="Resultados para: " + receivedData.title(), inline=False) 
                        await message.channel.send(embed=embed)
                except Exception as e:
                    embed = discord.Embed(title="Lista de stops demasiado grande, especifica melhor a quest/recompensa ou visita " + WEBSITE_URL, color=color)
                    await message.channel.send(embed=embed)
            elif len(returnedData) == 0:
                embed = discord.Embed(title="Não encontrei nenhum resultado para a tua pesquisa: "  + receivedData.title(), color=color)
                await message.channel.send(embed=embed)
            else:
                embed = discord.Embed(title="Lista de stops demasiado grande, especifica melhor a quest/recompensa ou visita " + WEBSITE_URL, color=color)
                await message.channel.send(embed=embed)

        elif message.content.startswith('!questmarinha'):
            receivedData = message.content.replace("!questmarinha ","")
            returnedData = find_quest(receivedData, 2)
            if returnedData == False:
                try:
                    await message.delete()
                    return
                except:
                    print('woops')
            
            if len(returnedData) > 0 and len(returnedData) < 25:
                try:
                    for data in returnedData:
                        # Initiate the discord main message
                        embed = discord.Embed(title=data["name"], url=data["map"], description=data["quest"], color=color)
                        # Set pokestop thumbnail image
                        embed.set_thumbnail(url=data["image"])
                        # Tag the author + add the search query
                        embed.add_field(name=message.author, value="Resultados para: " + receivedData.title(), inline=False) 
                        await message.channel.send(embed=embed)
                except Exception as e:
                    embed = discord.Embed(title="Lista de stops demasiado grande, especifica melhor a quest/recompensa ou visita " + WEBSITE_URL, color=color)
                    await message.channel.send(embed=embed)
            elif len(returnedData) == 0:
                embed = discord.Embed(title="Não encontrei nenhum resultado para a tua pesquisa: "  + receivedData.title(), color=color)
                await message.channel.send(embed=embed)
            else:
                embed = discord.Embed(title="Lista de stops demasiado grande, especifica melhor a quest/recompensa ou visita " + WEBSITE_URL, color=color)
                await message.channel.send(embed=embed)
        
        else:
            if message.author != client.user and str(message.author.id) not in allowedUsers:
                try:
                    await message.delete()
                except:
                    print('woops')

    if message.channel.id == convivioChannel:
        if message.content.startswith('!alertas'):
            jsonPokemonData = read_json_data()
            discordMessage = ""
            discordMessage = build_filter_message(discordMessage, jsonPokemonData)
            embed = discord.Embed(title="**Lista de Notificações de Pokémon**", description=discordMessage, color=color)
            await message.channel.send(embed=embed)

    if message.content.startswith('Mekye') and message.author.id == myId:
        await message.channel.send("Mekye meu dude")

def main_test():
    global fileFilter
    global fileQuest
    global fileVersion
    global fileScanned
    global fileClearQuests
    global modChannel
    global mapStatsChannel
    global questChannel
    global convivioChannel
    global messagesReceived
    global event_channel
    global messagesToDelete
    global authorToMute
    global muted_role
    global messagesFlaggedToBeDeleted

    modChannel = 896693003895328813
    questChannel = 896693003895328813
    convivioChannel = 896693003895328813
    event_channel = 896693003895328813
    #mapStatsChannel = 896693003895328813
    
    fileFilter = "filters.json"
    fileQuest = "quest_data.json"
    fileScanned = "scanned.pogoleiria"
    fileVersion = "version.pogoleiria"
    fileClearQuests = "clearquests.pogoleiria"

client.run(DISCORD_API_KEY)
