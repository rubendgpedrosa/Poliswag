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
from helpers.utilities import check_current_version, log_error

# Validates arguments passed to check what env was requested
if (len(sys.argv) != 2):
    print("Invalid number of arguments, usage: python3 main.py (dev|prod)")
    quit()

# Environment variables are loaded into memory here 
load_dotenv(prepare_environment(sys.argv[1]))

# Initialize global variables
globals.init()

comandoQuestsTitle = "__COMANDOS IMPLEMENTADOS:__"
comandoQuestsBody = "> !questleiria/questmarinha POKÉSTOP/QUEST/RECOMPENSA\nDevolve uma lista de resultados onde a pokéstop, quest ou recompensa correspondam ao texto inserido\n`(ex:!questmarinha startdust | !questleiria tribunal)`"

@tasks.loop(minutes=1)
async def prepare_daily_quest_message_task():
    file_exists_scanned = exists(globals.SCANNED_FILE)
    file_exists_version = check_current_version()
    file_exists_clear = exists(globals.CLEAR_QUESTS_FILE)
    color = random.randint(0, 16777215)

    if file_exists_scanned:
        try:
            channel = globals.CLIENT.get_channel(globals.QUEST_CHANNEL_ID)
            try:
                fetch_today_data()
                embed = discord.Embed(title="Scan de quests finalizado!", description="Todas as informações relacionadas com as quests foram recolhidas", color=color)
                embed.set_footer(text="Esta informação só é válida até ao final do dia")
                await channel.send(embed=embed)
                os.remove(globals.SCANNED_FILE)
            except Exception as e:
                log_error('FETCHING QUESTS ERROR: %s' % str(e))
                os.system("ps -ef | grep '/poliswag/main.py' | grep -v grep | awk '{print $2}' | xargs -r kill -9")
        except Exception as e:
            log_error('FETCHING QUESTS ERROR: %s' % str(e))
            os.system("ps -ef | grep '/poliswag/main.py' | grep -v grep | awk '{print $2}' | xargs -r kill -9")
    
    if file_exists_version:
        try:
            channel = globals.CLIENT.get_channel(globals.CONVIVIO_CHANNEL_ID)
            embed = discord.Embed(title="PAAAAUUUUUUUU!!! FORCE UPDATE!", description="Nova versão: 0." + globals.SAVED_VERSION, color=color)
            await channel.send(embed=embed)
        except Exception as e:
            log_error('\nFORCE UPDATE ERROR: %s\n' % str(e))

    if file_exists_clear:
        try:
            channel = globals.CLIENT.get_channel(globals.QUEST_CHANNEL_ID)
            embed = discord.Embed(title="Quests de hoje expiraram!", description="Lista de quests do dia anterior foi eliminada e a recolha das novas quests será feita durante a noite.", color=color)
            await channel.send(embed=embed)
            os.remove(globals.CLEAR_QUESTS_FILE)
        except Exception as e:
            log_error('\nERROR CLEARING QUESTS: %s\n' % str(e))

@globals.CLIENT.event
async def on_ready():
    prepare_daily_quest_message_task.start()

@globals.CLIENT.event
async def on_message(message):
    if message.author == globals.CLIENT.user:
        return

    color = random.randint(0, 16777215)

    if message.channel.id == globals.MAPSTATS_CHANNEL_ID:
        channel = globals.CLIENT.get_channel(globals.MAPSTATS_CHANNEL_ID)
        async for msg in channel.history(limit=200):
            if message != msg:
                await msg.delete()

    # Moderation commands to manage the pokemon scanner
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

            if message.content.startswith('!scan'):
                embed = discord.Embed(title="Rescan de pokestops inicializado", color=color)
                await message.channel.send(embed=embed)
                channel = globals.CLIENT.get_channel(globals.QUEST_CHANNEL_ID)
                await channel.send(embed=embed)
                os.system("bash /root/MAD-docker/scan.sh")

    # Quest channel commands in order do display quests
    if message.channel.id == globals.QUEST_CHANNEL_ID:
        if message.content.startswith('!comandos'):
            embed = discord.Embed(title=comandoQuestsTitle, description=comandoQuestsBody, color=color)
            await message.channel.send(embed=embed)

        if message.content.startswith('!questleiria') or message.content.startswith('!questmarinha'):
            leiria = False
            if message.content.startswith('!questleiria'):
                receivedData = message.content.replace("!questleiria ","")
                leiria = True
            else:
                receivedData = message.content.replace("!questmarinha ","")
            returnedData = find_quest(receivedData, leiria)

            if returnedData == False:
                try:
                    await message.delete()
                    return
                except Exception as e:
                    log_error('FAILED DUE TO EMPTY DATA: %s' % str(e))

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
                    log_error('FAILED IN QUEST ITERATION: %s' % str(e))
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
                except Exception as e:
                    log_error('FAILED DELETING MESSAGE IN QUEST CHANNEL: %s' % str(e))

    if message.channel.id == globals.CONVIVIO_CHANNEL_ID:
        if message.content.startswith('!alertas'):
            jsonPokemonData = read_json_data()
            discordMessage = build_filter_message("", jsonPokemonData)
            embed = discord.Embed(title="**Lista de Notificações de Pokémon**", description=discordMessage, color=color)
            await message.channel.send(embed=embed)

globals.CLIENT.run(globals.DISCORD_API_KEY)
