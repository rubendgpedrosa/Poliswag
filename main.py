#!/usr/bin/python\
import discord, sys, os
from discord.ext import tasks
from dotenv import load_dotenv

import helpers.constants as constants
import traceback

from helpers.poliswag import load_filter_data, build_commands_message, notify_accounts_available_message, decrement_and_notify_lure_count_by_username, update_playintegrity_job
from helpers.roles_manager import prepare_view_roles_location, restart_response_user_role_selection, build_rules_message, strip_user_roles
from helpers.quests import find_quest, write_filter_data, update_tracking_entries, list_track_quest
from helpers.utilities import check_current_pokemongo_version, clear_quest_file, log_to_file, build_embed_object_title_description, prepare_environment, validate_message_for_deletion, read_last_lines_from_log, clean_map_stats_channel, is_message_spam_message, remove_all_cached_messages_by_user
from helpers.scanner_manager import start_pokestop_scan, set_quest_scanning_state, restart_alarm_docker_container, start_quest_scanner_if_day_change, clear_quests_table, restart_map_container_if_scanning_stuck
from helpers.scanner_status import check_boxes_with_issues, is_quest_scanning_complete
from helpers.events import generate_database_entries_upcoming_events, ask_if_automatic_rescan_is_to_cancel, initialize_scheduled_rescanning_of_quests, notify_event_bonus_activated, restart_cancel_rescan_callback, retrieve_database_upcoming_events
from helpers.poligpt import get_response

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
            await generate_database_entries_upcoming_events()
            await initialize_scheduled_rescanning_of_quests()
            await ask_if_automatic_rescan_is_to_cancel()
            
            await is_quest_scanning_complete()
            await start_quest_scanner_if_day_change()
            #await check_force_expire_accounts_required()
    except Exception as e:
        error_msg = f"{str(e)}\n{traceback.format_exc()}"
        log_to_file(error_msg, "CRASH")

@constants.CLIENT.event
async def on_ready():
    __init__.start()

@constants.CLIENT.event
async def on_interaction(interaction):
    custom_id = interaction.data["custom_id"]
    
    if custom_id.startswith("Alertas") or custom_id in ["Leiria", "Marinha", "Remote", "Mystic", "Valor", "Instinct"]:
        await restart_response_user_role_selection(interaction)
    else:
        await restart_cancel_rescan_callback(interaction)

@constants.CLIENT.event
async def on_message(message):
    # Keeps the map status channel with the most recent message
    if message.channel.id == constants.MAPSTATS_CHANNEL_ID:
        await clean_map_stats_channel(message)
    
    if message.author == constants.CLIENT.user:
        return
    
    if validate_message_for_deletion(message.content, message.channel.id, message.author):
        await message.delete()

    messageToSend = ""
    if str(message.author.id) in constants.ADMIN_USERS_IDS:
        if message.content.startswith('!location'):
            await prepare_view_roles_location(message.channel)
        if message.content.startswith('!rules'):
            await build_rules_message(message)
            
    #if message.channel.id == 946803671881089095 and await is_message_spam_message(message):
    #    await strip_user_roles(message.author)
    #    await remove_all_cached_messages_by_user(message)

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
                messageToSend = build_embed_object_title_description("Alterações nas notificações efetuadas")

            if message.content.startswith('!quest'):
                set_quest_scanning_state(1)

            if message.content.startswith('!scan'):
                log_to_file(f"New quest scan requested by {message.author}")
                await message.channel.send(embed=build_embed_object_title_description(f"New quest scan requested by {message.author}"))
                start_pokestop_scan()
                messageToSend = build_embed_object_title_description(f"Scan quest has successfully started")

            if message.content.startswith('!logs'):
                messageToSend = build_embed_object_title_description("MOST RECENT LOGS", read_last_lines_from_log())
            
            # BETA FEATURE -> Too expensive to run in prod
            if message.content.startswith("!q") and constants.ENABLE_POLISWAGGPT:
                await message.channel.send(content=await get_response(message.content))
            
            if (message.content.startswith('!event')):
                await notify_event_bonus_activated()
                
            if (message.content.startswith('!lures')):
                await notify_accounts_available_message(message)
                
            if (message.content.startswith('!uselure')):
                await decrement_and_notify_lure_count_by_username(message)
                
            if (message.content.startswith('!questclear')):
                clear_quests_table()
                clear_quest_file()
                messageToSend = build_embed_object_title_description("Quest data cleared!", "Quest table and file have been cleared")

            if (message.content.startswith('!upcoming')):
                await retrieve_database_upcoming_events(message)
                
            if (message.content.startswith('!playintegrity')):
                await update_playintegrity_job(message)
            
            if message.content.startswith("!tracklist"):
                await list_track_quest(message.channel)
            
            if not message.content.startswith("!tracklist") and (message.content.startswith("!track") or message.content.startswith("!untrack") or message.content.startswith("!untrackall")):
                rewardToTrack = message.content.replace("!track ","") if message.content.startswith("!track") else message.content.replace("!untrack ","")
                update_tracking_entries(rewardToTrack, message)
                if message.content.startswith("!untrackall"):
                    messageToSend = build_embed_object_title_description(f"Successfully cleared tracking list")
                else:
                    messageToSend = build_embed_object_title_description(f"Successfully {'added' if message.content.startswith('!track') else 'removed'} {rewardToTrack} {'to' if message.content.startswith('!track') else 'from'} tracking list")
                
            if message.content.startswith("<@" + str(constants.POLISWAG_ID) + ">") or message.content.startswith(constants.POLISWAG_ROLE_ID):
                await build_commands_message(message)

    # Quest channel commands in order do display quests
    if message.channel.id == constants.QUEST_CHANNEL_ID:
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
        if message.content.startswith("!alertas"):
            messageToSend = load_filter_data()

    if messageToSend is not None and len(messageToSend) > 0:
        await message.channel.send(embed=messageToSend)

@constants.CLIENT.event
async def on_message_delete(message):
    if message.channel.id not in [constants.MOD_CHANNEL_ID, constants.QUEST_CHANNEL_ID, constants.MAPSTATS_CHANNEL_ID]:
        channel = constants.CLIENT.get_channel(constants.MOD_CHANNEL_ID)
        embed=discord.Embed(title=f"[{message.channel}] Mensagem removida", color=0x7b83b4)
        embed.add_field(name=message.author, value=message.content, inline=False)
        await channel.send(embed=embed)

constants.CLIENT.run(constants.DISCORD_API_KEY)