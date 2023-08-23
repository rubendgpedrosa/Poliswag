import discord, requests
from datetime import datetime
from discord.ui import Button, View

from helpers.database_connector import execute_query_to_database, get_data_from_database
from helpers.scanner_manager import start_pokestop_scan
from helpers.utilities import log_to_file, build_embed_object_title_description

import helpers.constants as constants

def handle_event_roles(message):
    if message.channel.id == constants.EVENT_CHANNEL_ID:
        if message.content.lower() == 'gold':
            event_role = discord.utils.get(message.guild.roles, name="Gold")
        elif message.content.lower() == 'silver':
            event_role = discord.utils.get(message.guild.roles, name="Silver")

import json

def read_local_events():
    with open(constants.EVENT_FILE, 'r') as file:
        data = json.load(file)
    return data

def read_active_events_data():
    try:
        events = read_local_events()
    except:
        # if there's an error reading the local file, fallback to making a request
        request = requests.get('https://raw.githubusercontent.com/ccev/pogoinfo/v2/active/events.json')
        events = request.json()
    return events
    
async def generate_database_entries_upcoming_events():
    events = read_active_events_data()
    for event in events:
        if event["start"] is not None:
            currentTime = datetime.now().strftime("%Y-%m-%d %H:%M")
            isBeforeStartDate = currentTime < event["start"]
            hasQuests = event["has_quests"]
            hasSpawnpoins = event["has_spawnpoints"]
            isNotCommunityDay = "community day" not in event["name"].lower()
            isNotGoEvent = not event["name"].startswith("GO")
            isNotUnannounced = "unannounced" not in event["name"].lower()
            isNotSpotlightHour = "spotlight hour" not in event["name"].lower()
            isNotRaidHour = "raid hour" not in event["name"].lower()

            if isBeforeStartDate and hasQuests and isNotCommunityDay and isNotGoEvent and isNotUnannounced and isNotSpotlightHour and isNotRaidHour:
                eventNameEscaped = event["name"].replace("'", "\\'")
                insertedRows = execute_query_to_database(f"INSERT IGNORE INTO event(name, start, end, has_quests, has_spawnpoints, rescan) VALUES ('{eventNameEscaped}', '{event['start']}', '{event['end']}', {int(hasQuests)}, {int(hasSpawnpoins)}, 1);", "poliswag")
                if insertedRows < 0:
                    await add_scheduled_event_discord_sidebar(event["name"], event["start"], event["end"])

async def ask_if_automatic_rescan_is_to_cancel():
    eventsByStartTime = get_events_stored_in_database_to_rescan()
    if len(eventsByStartTime) > 0:
        modChannel = constants.CLIENT.get_channel(constants.MOD_CHANNEL_ID)
        
        # Send message to mod channel with list of events at each start time
        for start_time, event_dict in eventsByStartTime.items():
            event_names = [event['name'] for event in event_dict['events']]
            body = "\n".join(event_names)
            embed = discord.Embed(title=f"PROXIMO RESCAN AGENDADO", color=0x7b83b4)
            embed.add_field(name=f"Horário: {start_time}", value=body, inline=False)
            buttonCancelRescan = Button(label="CANCELAR", style=discord.ButtonStyle.danger, custom_id=str(start_time), row=1)
            await add_button_event(buttonCancelRescan)
            view = View()
            view.add_item(buttonCancelRescan)
            
            await modChannel.send(embed=embed, view=view)
            execute_query_to_database(f"UPDATE event SET notifieddate = NOW() WHERE start = '{start_time}';", "poliswag")

def get_events_stored_in_database_to_rescan():
    storedEvents = get_data_from_database("SELECT name, start FROM event WHERE start > NOW() AND start < NOW() + INTERVAL 36 HOUR AND notifieddate IS NULL;", "poliswag")
    eventsDict = {}
    if len(storedEvents) > 0:
        for event in storedEvents:
            eventDict = {"name": event["data"][0], "start": event["data"][1]}
            if event["data"][1] in eventsDict:
                eventsDict[event["data"][1]]['events'].append(eventDict)
            else:
                eventsDict[event["data"][1]] = {'events': [eventDict]}
    return eventsDict

async def cancel_rescan_callback(interaction: discord.Interaction):
    try:
        start_time = interaction.data["custom_id"]
        # Your code to cancel the rescan for events at this start time
        await set_if_to_rescan_on_event_start(start_time, 0)
        await interaction.response.defer()
    except discord.errors.InteractionAlreadyResponded:
        pass
    else:
        embed=discord.Embed(title=f"RESCAN CANCELADO", description=f"{interaction.user} cancelou rescan automático!", color=0x7b83b4)
        await interaction.followup.send(embed=embed)
        log_to_file(f"Rescan cancelled by {interaction.user}")

async def initialize_scheduled_rescanning_of_quests():
    isQuestScanningScheduled = get_data_from_database("SELECT name FROM event WHERE rescan = 1 AND ((NOW() BETWEEN start AND DATE_ADD(start, INTERVAL 15 MINUTE) AND TIME(start) != '00:00:00') OR (NOW() BETWEEN end AND DATE_ADD(end, INTERVAL 15 MINUTE) AND TIME(end) != '00:00:00'));", "poliswag")
    if len(isQuestScanningScheduled) > 0:
        questScannerRunning = get_data_from_database("SELECT scanned FROM poliswag WHERE scanned = 0;", "poliswag")
        if len(questScannerRunning) > 0:
            log_to_file(f"Rescan scheduled starting")
            start_pokestop_scan()
            await notify_event_start()
            execute_query_to_database("UPDATE event SET rescan = 0 WHERE notifieddate IS NULL AND NOW() > DATE_SUB(start, INTERVAL 1 DAY);", "poliswag")
            log_to_file(f"Scheduled rescan started successfully")
    return

async def set_if_to_rescan_on_event_start(date, rescan = 0):
    execute_query_to_database(f"UPDATE event SET rescan = '{rescan}', updateddate = NOW() WHERE start = '{date}';", "poliswag")
    
async def add_button_event(button):
    button.callback = cancel_rescan_callback
    
async def notify_event_start():
    await notify_quest_channel_scan_start()
    await notify_event_bonus_activated()

async def notify_quest_channel_scan_start():
    questChannel = constants.CLIENT.get_channel(constants.QUEST_CHANNEL_ID)
    await questChannel.send(embed=build_embed_object_title_description("Alteração nas quests detetada", "Novo scan inicializado!"))

async def notify_event_bonus_activated():
    convivioChannel = constants.CLIENT.get_channel(constants.CONVIVIO_CHANNEL_ID)
    with open(constants.EVENT_FILE, 'r') as f:
        events = json.load(f)
    
    neverNotified = True
    now = datetime.now()
    for event in events:
        activeEvents = []
        if event["start"] is None or event["end"] is None:
            continue
        start = datetime.strptime(event["start"], "%Y-%m-%d %H:%M").replace(second=0, microsecond=0)
        end = datetime.strptime(event["end"], "%Y-%m-%d %H:%M").replace(second=0, microsecond=0)
        if start <= now <= end: #and event["has_quests"]
            for key, value in event.items():
                if key not in ["has_quests", "has_spawnpoints", "start", "end", "name", "type"] and value:
                    if key == "bonuses":
                        for bonus in value.split("#"):
                            activeEvents.append(f"• {bonus}")
                    else:
                        activeEvents.append(f"• {key.capitalize()}: {value}")
            content = ""
            if neverNotified:
                content = "**Atuais eventos ativos:**".upper()
                neverNotified = False
            await convivioChannel.send(
                content=content,
                embed=build_embed_object_title_description(event["name"].upper(), "\n".join(activeEvents), f"Entre {start} e {end}"),
            )
    if neverNotified:
        await convivioChannel.send(embed=build_embed_object_title_description("Paaaaauuuuuuuu...??", "Acabaram-se os eventos :("))

async def add_scheduled_event_discord_sidebar(eventName, startDate, endDate):
    await constants.CLIENT.create_scheduled_event(
        name=eventName,
        start_time=startDate,
        end_time=endDate
    )
