#!/usr/bin/python\
import os, sys, datetime

import discord
from bs4 import BeautifulSoup
from discord.ext import tasks
from dotenv import load_dotenv

import helpers.globals as globals
from helpers.notifications import load_filter_data, fetch_new_pvp_data
from helpers.roles_manager import prepare_view_roles_location, prepare_view_roles_teams, start_event_listeners, build_rules_message
from helpers.data_quests_handler import find_quest, write_filter_data, fetch_today_data, verify_quest_scan_done
from helpers.utilities import check_current_version, log_error, build_embed_object_title_description, prepare_environment, log_actions
from helpers.scanner_manager import rename_voice_channel, start_pokestop_scan, is_quest_scanning, set_quest_scanning_state

# Validates arguments passed to check what env was requested
if (len(sys.argv) != 2):
    print("Invalid number of arguments, usage: python3 main.py (dev|prod)")
    quit()

# Environment variables are loaded into memory here 
load_dotenv(prepare_environment(sys.argv[1]))

# Initialize global variables
globals.init()
fetch_new_pvp_data()

@tasks.loop(seconds=300)
async def __init__():
    log_actions("Proccess is running...")
    await check_current_version()
    await is_quest_scanning()

@globals.CLIENT.event
async def on_ready():
    __init__.start()

@globals.CLIENT.event
async def on_interaction(interaction):
    await start_event_listeners(interaction)

@globals.CLIENT.event
async def on_message(message):
    if message.author == globals.CLIENT.user:
        return

    if str(message.author.id) in globals.ADMIN_USERS_IDS:
        if message.content.startswith('!location'):
            await message.delete()
            await prepare_view_roles_location(message.channel)
        if message.content.startswith('!rules'):
            await message.delete()
            await build_rules_message(message)

    # Keeps the map status channel with the most recent message
    if message.channel.id == globals.MAPSTATS_CHANNEL_ID:
        channel = globals.CLIENT.get_channel(globals.MAPSTATS_CHANNEL_ID)
        async for msg in channel.history(limit=200):
            if message != msg:
                await msg.delete()

    # Moderation commands to manage the pokemon scanner
    if message.channel.id == globals.MOD_CHANNEL_ID:
        if str(message.author.id) in globals.ADMIN_USERS_IDS:
            if message.content.startswith('!add') or message.content.startswith('!remove'):
                await message.delete()
                if message.content.startswith('!add'):
                    receivedData = message.content.replace("!add ","")
                    add = True
                else:
                    receivedData = message.content.replace("!remove ","")
                    add = False
                receivedData = receivedData.split(" ", 1)
                returnedData = write_filter_data(receivedData, add)
                if returnedData == False:
                    await message.channel.send(embed=build_embed_object_title_description("Woops, parece que te enganaste migo."), delete_after=5)
                    return
                await message.channel.send(embed=build_embed_object_title_description(returnedData), delete_after=5)

            if message.content.startswith('!reload'):
                await message.delete()
                os.system('docker restart pokemon_alarm')
                await message.channel.send(embed=build_embed_object_title_description("Alterações nas Notificações efetuadas", "Faz @Poliswag Para ver a lista em vigor"), delete_after=5)

            if message.content.startswith('!scan'):
                await message.delete()
                start_pokestop_scan()
                await message.channel.send(embed=build_embed_object_title_description("Rescan de pokestops inicializado", "Este processo demora cerca de duas horas"), delete_after=5)
                channel = globals.CLIENT.get_channel(globals.QUEST_CHANNEL_ID)

    # Quest channel commands in order do display quests
    if message.channel.id == globals.QUEST_CHANNEL_ID:
        if message.content.startswith('!comandos'):
            await message.delete()
            await message.channel.send(embed=build_embed_object_title_description(
                "COMANDOS IMPLEMENTADOS", 
                "!questleiria/questmarinha POKÉSTOP/QUEST/RECOMPENSA\nDevolve uma lista de resultados onde a pokéstop, quest ou recompensa correspondam ao texto inserido",
                "(ex:!questmarinha startdust | !questleiria tribunal)")
            )

        if message.content.startswith('!questleiria') or message.content.startswith('!questmarinha'):
            leiria = False
            if message.content.startswith('!questleiria'):
                receivedData = message.content.replace("!questleiria ","")
                leiria = True
            else:
                receivedData = message.content.replace("!questmarinha ","")
            returnedData = find_quest(receivedData, leiria)
            if returnedData == False:
                return

            await message.delete()
            if len(returnedData) > 0 and len(returnedData) < 30:
                await message.channel.send(embed=build_embed_object_title_description("( " + message.author.name + " ) Resultados para: "  + message.content))
                for data in returnedData:
                    embed = discord.Embed(title=data["name"], url=data["map"], description=data["quest"], color=0x7b83b4)
                    embed.set_thumbnail(url=data["image"])
                    await message.channel.send(embed=embed)
            elif len(returnedData) == 0:
                await message.channel.send(embed=build_embed_object_title_description("( " + message.author.name + " ) Sem resultados para: " + message.content))
            else:
                await message.channel.send(embed=build_embed_object_title_description("Lista de stops demasiado grande, especifica melhor a quest/recompensa ou visita " + globals.WEBSITE_URL))
        else:
            if message.author != globals.CLIENT.user and str(message.author.id) not in globals.ADMIN_USERS_IDS:
                await message.delete()

    if message.channel.id == globals.CONVIVIO_CHANNEL_ID or message.channel.id == globals.MOD_CHANNEL_ID:
        if message.content == ("<@" + str(globals.POLISWAG_ID) + ">"):
            await message.delete()
            await message.channel.send(embed=load_filter_data(message.channel.id == globals.MOD_CHANNEL_ID), delete_after=300)

globals.CLIENT.run(globals.DISCORD_API_KEY)
