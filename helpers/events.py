import discord, requests
from datetime import datetime
from discord.ui import Button, View

from helpers.utilities import run_database_query, log_to_file, clear_quest_file, get_data_from_database
from helpers.scanner_manager import start_pokestop_scan
import helpers.constants as constants

def handle_event_roles(message):
    if message.channel.id == constants.EVENT_CHANNEL_ID:
        if message.content.lower() == 'gold':
            event_role = discord.utils.get(message.guild.roles, name="Gold")
        elif message.content.lower() == 'silver':
            event_role = discord.utils.get(message.guild.roles, name="Silver")

def make_request_events():
    request = requests.get('https://raw.githubusercontent.com/ccev/pogoinfo/v2/active/events.json')
    return request.json()
    
def generate_database_entries_upcoming_events():
    events = make_request_events()
    for event in events:
        if event["start"] is not None:
            currentTime = datetime.now().strftime("%Y-%m-%d %H:%M")
            isBeforeStartDate = currentTime < event["start"]
            hasQuests = event["has_quests"]
            hasSpawnpoins = event["has_spawnpoints"]
            isNotCommunityDay = "community day" not in event["name"].lower()
            isNotGoEvent = not event["name"].startswith("GO")

            if hasQuests and isNotCommunityDay and isNotGoEvent:
                run_database_query(f"INSERT IGNORE INTO event(name, start, end, has_quests, has_spawnpoints, rescan) VALUES ('{event['name']}', '{event['start']}', '{event['end']}', {int(hasQuests)}, {int(hasSpawnpoins)}, 1);", "poliswag")

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
            buttonCancelRescan = Button(label="CANCELAR", style=discord.ButtonStyle.danger, custom_id=start_time, row=1)
            await add_button_event(buttonCancelRescan)
            view = View()
            view.add_item(buttonCancelRescan)
            print(body)
            
            await modChannel.send(embed=embed, view=view)
            run_database_query(f"UPDATE event SET notifieddate = NOW() WHERE start = {start_time};", "poliswag")

def get_events_stored_in_database_to_rescan():
    storedEvents = run_database_query("SELECT name, start FROM event WHERE notifieddate IS NULL AND NOW() > DATE_SUB(start, INTERVAL 24 HOUR);", "poliswag")
    storedEvents = str(storedEvents).split("\\n")
    eventsDict = {}
    del storedEvents[0]
    if len(storedEvents) > 1:
        del storedEvents[len(storedEvents) - 1]
        for event in storedEvents:
            fields = event.split("\\t")
            eventDict = {"name": fields[0], "start": fields[1]}
            if fields[1] in eventsDict:
                eventsDict[fields[1]]['events'].append(eventDict)
            else:
                eventsDict[fields[1]] = {'events': [eventDict]}
    
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

def initialize_scheduled_rescanning_of_quests():
    isQuestScanningScheduled = get_data_from_database("SELECT name FROM event WHERE rescan = 1 AND (NOW() BETWEEN start AND DATE_ADD(start, INTERVAL  15 MINUTE) OR NOW() BETWEEN end AND DATE_ADD(end, INTERVAL  15 MINUTE));", "poliswag")
    if isQuestScanningScheduled != "":
        questScannerRunning = get_data_from_database("SELECT scanned FROM poliswag;", "poliswag")
        if questScannerRunning == 0:
            log_to_file(f"Rescan scheduled starting")
            start_pokestop_scan()
            run_database_query("UPDATE event SET rescan  notifieddate IS NULL AND NOW() > DATE_SUB(start, INTERVAL 24 HOUR);", "poliswag")
            log_to_file(f"Scheduled rescan started successfully")
    return

async def set_if_to_rescan_on_event_start(date, rescan = 0):
    run_database_query(f"UPDATE event SET rescan = {rescan}, updateddate = NOW() WHERE start = '{date}';", "poliswag")
    
async def add_button_event(button):
    button.callback = cancel_rescan_callback
