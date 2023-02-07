import discord, requests
from datetime import datetime
from discord.ui import Button, View
import hashlib

from helpers.utilities import run_database_query, log_to_file, clear_quest_file
from helpers.scanner_manager import start_pokestop_scan
import helpers.constants as constants

# CREATE TABLE event (
#   name VARCHAR(255) NOT NULL,
#   start DATETIME,
#   end DATETIME,
#   spawns VARCHAR(50),
#   eggs VARCHAR(50),
#   raids VARCHAR(50),
#   shinies VARCHAR(50),
#   bonuses VARCHAR(50),
#   features VARCHAR(50),
#   has_quests BOOLEAN DEFAULT 0,
#   has_spawnpoints BOOLEAN DEFAULT 0,
#   rescan BOOLEAN DEFAULT 0,
#   updateddate DATETIME,
#   notifiieddate DATETIME,
#   PRIMARY KEY (name, start)
# )

def handle_event_roles(message):
    if message.channel.id == constants.EVENT_CHANNEL_ID:
        if message.content.lower() == 'gold':
            event_role = discord.utils.get(message.guild.roles, name="Gold")
        elif message.content.lower() == 'silver':
            event_role = discord.utils.get(message.guild.roles, name="Silver")
        #elif str(message.author.id) not in ADMIN_USERS_IDS:
            #await message.delete()
    #await message.author.add_roles(event_role, atomic=True)
    #await message.add_reaction("✅")

def fetch_events():
    request = requests.get('https://raw.githubusercontent.com/ccev/pogoinfo/v2/active/events.json')
    return request.json()
    
def get_events_by_date():
    events = fetch_events()
    for event in events:
        if event["start"] is not None:
            if datetime.now().strftime("%Y-%m-%d %H:%M") < event["start"] and event["has_quests"] and not event["name"].startswith("GO"):
                hashString = hashlib.sha1((event['start'] + event['end'] + event['name']).encode("UTF-8")).hexdigest()
                run_database_query(f"INSERT IGNORE INTO event(id, name, start, end, has_quests, has_spawnpoints) VALUES{hashString[:50], event['name'], event['start'], event['end'], +(event['has_quests']), +(event['has_spawnpoints'])};", "poliswag")
    # events = sorted(events, key=lambda d: d["start"], reverse=True)
    return events

async def set_automatic_rescan_on_event_change(id, rescan = 0):
    run_database_query(f"UPDATE event SET rescan = {rescan}, updateddate = NOW() WHERE id = '{id}';", "poliswag")

def get_automatic_rescan_on_event_change():
    listEvents = []
    events = run_database_query("SELECT id, name, start, end, rescan, updateddate FROM event WHERE updateddate IS NULL AND notifieddate IS NULL AND NOW() > DATE_SUB(start, INTERVAL 24 HOUR);", "poliswag");
    events = str(events).split("\\n")
    # Remove first and last element
    del events[0]
    if len(events) > 1:
        del events[len(events) - 1]
        keyValues = ["id", "name", "start", "end", "rescan", "updateddate"]
        for event in events:
            event = event.split("\\t")
            # Convers de list of values into a dict by using keyValues as keys
            listEvents.append(dict(zip(keyValues, event)))
        #for eventToDecode in listEvents:
        #    eventToDecode["name"] = eventToDecode["name"].decode(encoding='UTF-8',errors='strict')
        return listEvents
    return []

async def validate_event_needs_automatic_scan():
    events = get_automatic_rescan_on_event_change()
    if len(events) > 0:
        modChannel = constants.CLIENT.get_channel(constants.MOD_CHANNEL_ID)
        for event in events:
            event["name"] = event["name"].replace("\\/", "/").encode().decode('unicode_escape')
            log_to_file(f"Event {event['name']} needs rescan validation")
            buttonScheduleRescan = Button(label="CONFIRMAR RESCAN", style=discord.ButtonStyle.primary, custom_id=event["id"], row=1)
            await add_button_event(buttonScheduleRescan)
            view = View()
            view.add_item(buttonScheduleRescan)

            embed=discord.Embed(title=f"RESCAN AGENDADO REQUER CONFIRMAÇÃO", color=0x7b83b4)
            embed.add_field(name=f"{event['name']}", value=f"Horário: {event['start']}", inline=False)

            await modChannel.send(embed=embed, view=view)
            run_database_query(f"UPDATE event SET notifieddate = NOW() WHERE id = '{event['id']}';", "poliswag");
    
async def add_button_event(button):
    button.callback = confirm_scheduled_rescan

async def confirm_scheduled_rescan(interaction):
    #https://discordpy.readthedocs.io/en/stable/interactions/api.html
    #await edit_message(*, content=..., embed=..., embeds=..., attachments=..., view=..., allowed_mentions=...)
    await set_automatic_rescan_on_event_change(interaction.data["custom_id"], 1)
    embed=discord.Embed(title=f"CONFIRMAÇÃO PARA RESCAN AGENDADO", description=f"{interaction.user} confirmou o rescan automático!", color=0x7b83b4)
    await interaction.channel.send(embed=embed)
    log_to_file(f"Automatic rescan for {interaction.data['custom_id']} enabled by {interaction.user}")

def get_event_to_schedule_rescan():
    scheduledRescanIsEnabled = run_database_query("SELECT name FROM event WHERE updateddate IS NOT NULL AND rescan = 1 AND (NOW() BETWEEN start AND DATE_ADD(start, INTERVAL 30 MINUTE));", "poliswag")
    if len(str(scheduledRescanIsEnabled).split("\\n")) > 1:
        run_database_query("UPDATE event SET rescan = 0 WHERE updateddate IS NOT NULL AND rescan = 1 AND (NOW() BETWEEN start AND DATE_ADD(start, INTERVAL 30 MINUTE));", "poliswag")
        clear_quest_file()
        start_pokestop_scan()
        log_to_file(f"Rescanning initialized from scheduled event")
