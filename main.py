#!/usr/bin/python\
import discord, sys
from discord.ext import tasks
from dotenv import load_dotenv

import helpers.constants as constants

from helpers.poliswag import load_filter_data
from helpers.roles_manager import prepare_view_roles_location, start_event_listeners, build_rules_message
from helpers.quests import find_quest, write_filter_data
from helpers.utilities import check_current_pokemongo_version, log_to_file, build_embed_object_title_description, prepare_environment, validate_message_for_deletion, read_last_lines_from_log
from helpers.scanner_manager import start_pokestop_scan, set_quest_scanning_state, restart_alarm_docker_container, start_quest_scanner_if_day_change
from helpers.scanner_status import check_boxes_with_issues, is_quest_scanning_complete, restart_map_container_if_scanning_stuck
from helpers.events import get_events_by_date, validate_event_needs_automatic_scan, get_event_to_schedule_rescan

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
    try:
        await check_current_pokemongo_version()
        await check_boxes_with_issues()
        
        scanningStuck = await restart_map_container_if_scanning_stuck()
        if not scanningStuck:
            get_events_by_date()
            get_event_to_schedule_rescan()
            await validate_event_needs_automatic_scan()
            
            start_quest_scanner_if_day_change()
            await is_quest_scanning_complete()
    except Exception as e:
        log_to_file('%s' % str(e), "ERROR") 


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

    messageToSend = ""
    if str(message.author.id) in constants.ADMIN_USERS_IDS:
        if message.content.startswith('!location'):
            await prepare_view_roles_location(message.channel)
        if message.content.startswith('!rules'):
            await build_rules_message(message)

    # Keeps the map status channel with the most recent message
    if message.channel.id == constants.MAPSTATS_CHANNEL_ID:
        channel = constants.CLIENT.get_channel(constants.MAPSTATS_CHANNEL_ID)
        async for msg in channel.history(limit=200):
            if message != msg and str(message.author.id) not in constants.ADMIN_USERS_IDS:
                await msg.delete()

    # Moderation commands to manage the pokemon scanner
    if message.channel.id == constants.MOD_CHANNEL_ID:
        if str(message.author.id) in constants.ADMIN_USERS_IDS:
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
                    messageToSend = build_embed_object_title_description("Woops, parece que te enganaste migo.")
                else:
                    messageToSend = build_embed_object_title_description(returnedData)

            if message.content.startswith('!reload'):
                log_to_file(f"Notification filters reloaded by {message.author}")
                restart_alarm_docker_container()
                messageToSend = build_embed_object_title_description("Alterações nas notificações efetuadas", "Menciona @Poliswag Para ver a lista em vigor")

            if message.content.startswith('!quest'):
                set_quest_scanning_state(1)

            if message.content.startswith('!scan'):
                log_to_file(f"New quest scan requested by {message.author}")
                await message.channel.send(embed=build_embed_object_title_description(f"New quest scan requested by {message.author}"), delete_after=300)
                start_pokestop_scan()
                messageToSend = build_embed_object_title_description(f"Scan quest has successfully started")

            if message.content.startswith('!logs'):
                messageToSend = build_embed_object_title_description("MOST RECENT LOGS", read_last_lines_from_log())

    # Quest channel commands in order do display quests
    if message.channel.id == constants.QUEST_CHANNEL_ID:
        if message.content.startswith('!comandos'):
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

    if message.channel.id == constants.CONVIVIO_CHANNEL_ID or message.channel.id == constants.MOD_CHANNEL_ID:
        if message.content.startswith("<@" + str(constants.POLISWAG_ID) + ">") or message.content.startswith(constants.POLISWAG_ROLE_ID):
            messageToSend = load_filter_data(message.channel.id == constants.MOD_CHANNEL_ID)
    
    if validate_message_for_deletion(message.content, message.channel.id, message.author):
        await message.delete()

    if messageToSend is not None and len(messageToSend) > 0:
        await message.channel.send(embed=messageToSend, delete_after=300)

@constants.CLIENT.event
async def on_message_delete(message):
    if message.channel.id not in [constants.MOD_CHANNEL_ID, constants.QUEST_CHANNEL_ID, constants.MAPSTATS_CHANNEL_ID]:
        channel = constants.CLIENT.get_channel(constants.MOD_CHANNEL_ID)
        embed=discord.Embed(title=f"[{message.channel}] Mensagem removida", color=0x7b83b4)
        embed.add_field(name=message.author, value=message.content, inline=False)
        await channel.send(embed=embed)

constants.CLIENT.run(constants.DISCORD_API_KEY)
