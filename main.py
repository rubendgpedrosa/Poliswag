#!/usr/bin/python\
import discord, sys
from discord.ext import tasks
from dotenv import load_dotenv

import helpers.constants as constants

from helpers.poliswag import load_filter_data
from helpers.roles_manager import prepare_view_roles_location, start_event_listeners, build_rules_message
from helpers.quests import find_quest, write_filter_data, fetch_today_data
from helpers.utilities import check_current_version, log_error, build_embed_object_title_description, prepare_environment
from helpers.scanner_manager import start_pokestop_scan, set_quest_scanning_state, restart_alarm_docker_container
from helpers.scanner_status import check_boxes_issues, is_quest_scanning

# Validates arguments passed to check what env was requested
if (len(sys.argv) != 2):
    print("Invalid number of arguments, usage: python3 main.py (dev|prod)")
    quit()

# Environment variables are loaded into memory here 
load_dotenv(prepare_environment(sys.argv[1]))

# Initialize global variables
constants.init()

@tasks.loop(seconds=300)
async def __init__():
    await check_current_version()
    await is_quest_scanning()
    await check_boxes_issues()
    fetch_today_data()

@constants.CLIENT.event
async def on_ready():
    __init__.start()

@constants.CLIENT.event
async def on_interaction(interaction):
    await start_event_listeners(interaction)

@constants.CLIENT.event
async def on_message(message):
    if message.author == constants.CLIENT.user:
        return

    if str(message.author.id) in constants.ADMIN_USERS_IDS:
        if message.content.startswith('!location'):
            await message.delete()
            await prepare_view_roles_location(message.channel)
        if message.content.startswith('!rules'):
            await message.delete()
            await build_rules_message(message)

    # Keeps the map status channel with the most recent message
    if message.channel.id == constants.MAPSTATS_CHANNEL_ID:
        channel = constants.CLIENT.get_channel(constants.MAPSTATS_CHANNEL_ID)
        async for msg in channel.history(limit=200):
            if message != msg:
                await msg.delete()

    # Moderation commands to manage the pokemon scanner
    if message.channel.id == constants.MOD_CHANNEL_ID:
        if str(message.author.id) in constants.ADMIN_USERS_IDS:
            await message.delete()
            # TODO: Change message
            if message.content.startswith('!add') or message.content.startswith('!remove'):
                if message.content.startswith('!add'):
                    receivedData = message.content.replace("!add ","")
                    add = True
                else:
                    receivedData = message.content.replace("!remove ","")
                    add = False
                receivedData = receivedData.split(" ", 1)
                returnedData = write_filter_data(receivedData, add)
                if returnedData == False:
                    await message.channel.send(embed=build_embed_object_title_description("Woops, parece que te enganaste migo."), delete_after=30)
                    return
                await message.channel.send(embed=build_embed_object_title_description(returnedData), delete_after=30)

            if message.content.startswith('!reload'):
                restart_alarm_docker_container()
                await message.channel.send(embed=build_embed_object_title_description("Alterações nas Notificações efetuadas", "Faz @Poliswag Para ver a lista em vigor"), delete_after=30)

            if message.content.startswith('!quest'):
                set_quest_scanning_state(1)

            if message.content.startswith('!scan'):
                try:
                    start_pokestop_scan()
                    await message.channel.send(embed=build_embed_object_title_description("Rescan de pokestops inicializado", "Este processo demora cerca de duas horas"), delete_after=30)
                    channel = constants.CLIENT.get_channel(constants.QUEST_CHANNEL_ID)
                except Exception as e:
                    log_error('Quest scanning log failed: %s' % str(e))       
                    
    # Quest channel commands in order do display quests
    if message.channel.id == constants.QUEST_CHANNEL_ID:
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
                await message.channel.send(embed=build_embed_object_title_description("Lista de stops demasiado grande, especifica melhor a quest/recompensa ou visita " + constants.WEBSITE_URL))
        else:
            if message.author != constants.CLIENT.user and str(message.author.id) not in constants.ADMIN_USERS_IDS:
                await message.delete()

    if message.channel.id == constants.CONVIVIO_CHANNEL_ID or message.channel.id == constants.MOD_CHANNEL_ID:
        if message.content == ("<@" + str(constants.POLISWAG_ID) + ">"):
            await message.delete()
            await message.channel.send(embed=load_filter_data(message.channel.id == constants.MOD_CHANNEL_ID), delete_after=300)

@constants.CLIENT.event
async def on_message_delete(message):
    if message.channel.id not in [constants.MOD_CHANNEL_ID, constants.QUEST_CHANNEL_ID, constants.MAPSTATS_CHANNEL_ID]:
        channel = constants.CLIENT.get_channel(constants.MOD_CHANNEL_ID)
        await channel.send(embed=build_embed_object_title_description(
            f"Mensagem removida no canal {message.channel} enviada por {message.author}", 
            message.content
            )
        )

constants.CLIENT.run(constants.DISCORD_API_KEY)
