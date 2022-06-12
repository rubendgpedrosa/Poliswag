#!/usr/bin/python\
import json, os, requests, random, sys
from os.path import exists

import discord
from bs4 import BeautifulSoup
from discord.ext import tasks
from dotenv import load_dotenv

import helpers.globals as globals
from helpers.notifications import load_filter_data, read_json_data, build_filter_message
from helpers.environment import prepare_environment
from helpers.quests import fetch_today_data, find_quest, write_filter_data

# Validates arguments passed to check what env was requested
if (len(sys.argv) != 2):
    print("Invalid number of arguments, usage: python3 main.py (dev|prod)")
    quit()

# Environment variables are loaded into memory here 
load_dotenv(prepare_environment(sys.argv[1]))

globals.init()

pogoleiriaurl = globals.WEBSITE_URL + "static/images/header/pogoleiria_rounded.png"
muted_role = ""

comandoQuestsTitle = "__COMANDOS IMPLEMENTADOS:__"
comandoQuestsBody = "> !questleiria/questmarinha POKÉSTOP/QUEST/RECOMPENSA\nDevolve uma lista de resultados onde a pokéstop, quest ou recompensa correspondam ao texto inserido\n`(ex:!questmarinha startdust | !questleiria tribunal)`"

def get_version():
    #with open('savedVersion.txt', 'r') as data:
    with open(globals.VERSION_FILE, 'r') as file:
        data = file.read().rstrip()
    return data

@tasks.loop(minutes=1)
async def prepare_daily_quest_message_task():
    file_exists_scanned = exists(globals.SCANNED_FILE)
    file_exists_version = exists(globals.VERSION_FILE)
    file_exists_clear = exists(globals.CLEAR_QUESTS_FILE)

    if file_exists_scanned:
        color = random.randint(0, 16777215)
        try:
            channel = globals.CLIENT.get_channel(globals.QUEST_CHANNEL_ID)
            try:
                fetch_today_data()
                embed = discord.Embed(title="Scan de quests finalizado!", description="Todas as informações relacionadas com as quests foram recolhidas", color=color)
                embed.set_footer(text="Esta informação só é válida até ao final do dia")
                await channel.send(embed=embed)
                os.remove(globals.SCANNED_FILE)
            except OSError as e:
                f = open(globals.LOG_FILE, 'w')
                f.write('FETCHING QUESTS ERROR: %s' % str(e))
                f.close()
                os.system("ps -ef | grep '/poliswag/main.py' | grep -v grep | awk '{print $2}' | xargs -r kill -9")
        except OSError as e:
            f = open(globals.LOG_FILE, 'w')
            f.write('FETCHING QUESTS ERROR: %s' % str(e))
            f.close()
            os.system("ps -ef | grep '/poliswag/main.py' | grep -v grep | awk '{print $2}' | xargs -r kill -9")
    
    if file_exists_version:
        color = random.randint(0, 16777215)
        try:
            channel = globals.CLIENT.get_channel(globals.CONVIVIO_CHANNEL_ID)
            embed = discord.Embed(title="PAAAAUUUUUUUU!!! FORCE UPDATE!", description="Nova versão: " + get_version(), color=color)
            await channel.send(embed=embed)
            os.remove(globals.VERSION_FILE)
        except OSError as e:
            f = open(globals.LOG_FILE, 'w')
            f.write('\nFORCE UPDATE ERROR: %s\n' % str(e))
            f.close()

    if file_exists_clear:
        color = random.randint(0, 16777215)
        try:
            channel = globals.CLIENT.get_channel(globals.QUEST_CHANNEL_ID)
            embed = discord.Embed(title="Quests de hoje expiraram!", description="Lista de quests do dia anterior foi eliminada e a recolha das novas quests será feita durante a noite.", color=color)
            await channel.send(embed=embed)
            os.remove(globals.CLEAR_QUESTS_FILE)
        except OSError as e:
            f = open(globals.LOG_FILE, 'w')
            f.write('\nErro a a limpar quests: %s\n' % str(e))
            f.close()

@globals.CLIENT.event
async def on_ready():
    prepare_daily_quest_message_task.start()

@globals.CLIENT.event
async def on_message(message):
    if message.author == globals.CLIENT.user:
        return

    muted_role = discord.utils.get(message.guild.roles, name="Muted")

    color = random.randint(0, 16777215)

    if message.channel.id == globals.MAPSTATS_CHANNEL_ID:
        channel = globals.CLIENT.get_channel(globals.MAPSTATS_CHANNEL_ID)
        async for msg in channel.history(limit=200):
            if message != msg:
                await msg.delete()

    if message.channel.id == globals.MOD_CHANNEL_ID:
        if str(message.author.id) in globals.ADMIN_USERS_IDS:
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
                channel = globals.CLIENT.get_channel(globals.CONVIVIO_CHANNEL_ID)
                await channel.send(embed=embed)

            if message.content.startswith('!scan'):
                embed = discord.Embed(title="Rescan de pokestops inicializado", color=color)
                await message.channel.send(embed=embed)
                channel = globals.CLIENT.get_channel(globals.QUEST_CHANNEL_ID)
                await channel.send(embed=embed)
                os.system("bash /root/MAD-docker/scan.sh")

    if message.channel.id == globals.QUEST_CHANNEL_ID:
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
                    embed = discord.Embed(title="Lista de stops demasiado grande, especifica melhor a quest/recompensa ou visita " + globals.WEBSITE_URL, color=color)
                    await message.channel.send(embed=embed)
            elif len(returnedData) == 0:
                embed = discord.Embed(title="Não encontrei nenhum resultado para a tua pesquisa: "  + receivedData.title(), color=color)
                await message.channel.send(embed=embed)
            else:
                embed = discord.Embed(title="Lista de stops demasiado grande, especifica melhor a quest/recompensa ou visita " + globals.WEBSITE_URL, color=color)
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
                    embed = discord.Embed(title="Lista de stops demasiado grande, especifica melhor a quest/recompensa ou visita " + globals.WEBSITE_URL, color=color)
                    await message.channel.send(embed=embed)
            elif len(returnedData) == 0:
                embed = discord.Embed(title="Não encontrei nenhum resultado para a tua pesquisa: "  + receivedData.title(), color=color)
                await message.channel.send(embed=embed)
            else:
                embed = discord.Embed(title="Lista de stops demasiado grande, especifica melhor a quest/recompensa ou visita " + globals.WEBSITE_URL, color=color)
                await message.channel.send(embed=embed)
        
        else:
            if message.author != globals.CLIENT.user and str(message.author.id) not in globals.ADMIN_USERS_IDS:
                try:
                    await message.delete()
                except:
                    print('woops')

    if message.channel.id == globals.CONVIVIO_CHANNEL_ID:
        if message.content.startswith('!alertas'):
            jsonPokemonData = read_json_data()
            discordMessage = ""
            discordMessage = build_filter_message(discordMessage, jsonPokemonData)
            embed = discord.Embed(title="**Lista de Notificações de Pokémon**", description=discordMessage, color=color)
            await message.channel.send(embed=embed)

globals.CLIENT.run(globals.DISCORD_API_KEY)
