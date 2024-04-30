import helpers.constants as constants
import discord
import datetime, requests
from bs4 import BeautifulSoup
import pytz

from helpers.utilities import build_embed_object_title_description, log_to_file, build_embed_object_title_description, clean_map_stats_channel
from helpers.scanner_manager import set_quest_scanning_state, restart_run_docker_containers, revert_device_scanning_marinha
from helpers.database_connector import execute_query_to_database, get_data_from_database
from helpers.quests import get_current_quest_data, retrieve_quest_summary

# current datetime lisbon timezone
CURRENT_DATETIME = datetime.datetime.now() - datetime.timedelta(minutes=20)

boxUsersMapping = {
    "Tx9s": "<@98846248865398784>",
    "a95xF1": "<@98846248865398784>",
    "Poco": "<@98846248865398784>",
    "Tx9s1_JMBoy": "<@308000681271492610>",
    "Tx9s2_JMBoy": "<@308000681271492610>",
    "Tx9s3_JMBoy": "<@308000681271492610>",
    "Tx9s_Ethix": "<@313738342904627200>",
    "Tx9s_Anakin": "<@339466204638871552>"
}

# default time = current time
currentActiveTimeDevices = {
    "Tx9s": CURRENT_DATETIME,
    "a95xF1": CURRENT_DATETIME,
    "Poco": CURRENT_DATETIME,
    "Tx9s1_JMBoy": CURRENT_DATETIME,
    "Tx9s2_JMBoy": CURRENT_DATETIME,
    "Tx9s3_JMBoy": CURRENT_DATETIME,
    "Tx9s_Ethix": CURRENT_DATETIME,
    "Tx9s_Anakin": CURRENT_DATETIME
}

devicesWereLastSeen = {}

cachedDevicesNotScanning = []

async def check_boxes_with_issues():
    read_mad_log_file()
    await notify_devices_failing()
    
def read_mad_log_file():
    global currentActiveTimeDevices, devicesWereLastSeen
    devicesWereLastSeen = {
        "Tx9s": False,
        "a95xF1": False,
        "Poco": False,
        "Tx9s1_JMBoy": False,
        "Tx9s2_JMBoy": False,
        "Tx9s3_JMBoy": False,
        "Tx9s_Ethix": False,
        "Tx9s_Anakin": False
    }
    
    deviceNames = devicesWereLastSeen.keys()
    
    with open(constants.MAD_LOG_FILE, 'r') as file:
        lines = file.readlines()
        lastLines = lines[-200:]
        for line in lastLines:
            for device in deviceNames:
                if f"{device}]" in line:                        
                    devicesWereLastSeen[device] = "origin is no longer connected" not in line
                    if "Got data of type ReceivedType." in line:
                        currentActiveTimeDevices[device] = datetime.datetime.now()
    
async def notify_devices_failing():
    totalBoxesFailing = []
    totalDevicesNotScanning = []
    for boxName in devicesWereLastSeen:
        if not devicesWereLastSeen[boxName]:
            totalBoxesFailing.append({"data": [boxName]})
    for boxName in currentActiveTimeDevices:
        if datetime.datetime.now() - currentActiveTimeDevices[boxName] > datetime.timedelta(seconds=1200):
            totalDevicesNotScanning.append({"data": [boxName]})
            
    if len(totalBoxesFailing) > 0:
        await notify_devices_down(totalBoxesFailing)
    else:
        await clean_map_stats_channel("clear")
    
    if len(totalDevicesNotScanning) > 0:
        await notify_devices_not_scanning(totalDevicesNotScanning)
        await rename_voice_channels(totalDevicesNotScanning)
    else:
        await rename_voice_channels([])

async def notify_devices_not_scanning(totalDevicesNotScanning):
    global cachedDevicesNotScanning
    newDevicesNotScanning = []
    for device in totalDevicesNotScanning:
        if device not in cachedDevicesNotScanning:
            newDevicesNotScanning.append(device)
            cachedDevicesNotScanning.append(device)
    
    if len(newDevicesNotScanning) == 0:
        return
    
    content = " ".join([boxName["data"][0] for boxName in newDevicesNotScanning])
    
    embed = discord.Embed(
        title=":warning: DEVICES NOT SCANNING :warning:",
        color=0x7b83b4
    )
    
    #log_to_file(f"Devices not scanning: {content}", "WARNING")
    
    user_id = 98846248865398784
    user = await constants.CLIENT.fetch_user(user_id)

    if user:
        try:
            await user.send(
                content=content,
                embed=embed
            )
        except discord.Forbidden:
            print(f"I don't have permission to send a direct message to {user.name}")
    else:
        print("User not found")
    
    
async def notify_devices_down(totalBoxesFailing):
    usersToNotify = []
    boxNamesToNotify = []
    for boxName in totalBoxesFailing:
        boxName = boxName["data"][0]
        userMention = boxUsersMapping.get(boxName)
        if userMention:
            if userMention not in usersToNotify:
                usersToNotify.append(userMention)
            boxNamesToNotify.append(boxName)

    usersToNotifyString = " ".join(usersToNotify)
    boxesNameJoined = ", ".join(boxNamesToNotify)
    boxesToNotifyString = boxesNameJoined + f" precisa{'' if len(boxNamesToNotify) == 1 else 'm'} de um reboot"

    channel = constants.CLIENT.get_channel(constants.MAPSTATS_CHANNEL_ID)
    messages = [message async for message in channel.history(limit=10)]
    for msg in messages:
        for embed in msg.embeds:
            if boxesToNotifyString in embed.footer.text:
                return
        
    await clean_map_stats_channel("clear")

    embed = discord.Embed(
        title=":warning: ANOMALIA DETETADA :warning:",
        color=0x7b83b4
    )
    
    embed.add_field(name="Ativadas", value=f":green_circle: {constants.TOTAL_BOXES - len(totalBoxesFailing)}/{constants.TOTAL_BOXES}", inline=False)
    embed.add_field(name="Desativadas", value=f":red_circle: {len(totalBoxesFailing)}/{constants.TOTAL_BOXES}", inline=False)

    embed.set_footer(text=boxesToNotifyString)

    await channel.send(
        content=usersToNotifyString,
        embed=embed
    )
    log_to_file(f"Devices not found: {boxesNameJoined}", "ERROR")

async def rename_voice_channels(totalBoxesFailing):
    leiriaVoiceChannel = constants.CLIENT.get_channel(constants.VOICE_CHANNEL_ID)
    marinhaVoiceChannel = constants.CLIENT.get_channel(constants.VOICE_CHANNEL_MARINHA_ID)

    leiriaDownCounter = sum(1 for boxName in totalBoxesFailing if split_list_by_region(boxName["data"][0]) == "Leiria")
    marinhaDownCounter = sum(1 for boxName in totalBoxesFailing if split_list_by_region(boxName["data"][0]) == "MarinhaGrande")

    leiriaStatus = get_status_message(leiriaDownCounter, "LEIRIA")
    marinhaStatus = get_status_message(marinhaDownCounter, "MARINHA")

    if leiriaVoiceChannel.name != leiriaStatus:
        await leiriaVoiceChannel.edit(name=leiriaStatus)

    if marinhaVoiceChannel.name != marinhaStatus:
        await marinhaVoiceChannel.edit(name=marinhaStatus)

def get_status_message(downCounter, region):
    if region == "LEIRIA":
        if downCounter > 5:
            return f"{region}: 🔴"
        elif downCounter > 2:
            return f"{region}: 🟠"
        elif downCounter > 0:
            return f"{region}: 🟡"
        else:
            return f"{region}: 🟢"
    elif region == "MARINHA":
        if downCounter > 2:
            return f"{region}: 🔴"
        elif downCounter > 0:
            return f"{region}: 🟠"
        else:
            return f"{region}: 🟢"
    return f"{region}: ⚪"

def split_list_by_region(boxName):
    if boxName == "Tx9s_Ethix" or boxName == "Tx9s_Anakin":
        return "MarinhaGrande"
    else:
        return "Leiria"

async def restart_map_container_if_scanning_stuck():
    lisbonTz = pytz.timezone('Europe/Lisbon')
    currentTime = datetime.datetime.now(lisbonTz)
    dstTimeChanges = 0
    if currentTime.dst() != datetime.timedelta(0):
        dstTimeChanges = 60
    pokemonScanResults = get_data_from_database(f"SELECT IF(last_updated >= NOW() - INTERVAL {constants.TIME_MULTIPLIER * (dstTimeChanges + 30)} MINUTE, '', 'Stuck') AS status FROM pokestop ORDER BY last_updated DESC LIMIT 1;")
    for pokemonScanResult in pokemonScanResults:
        if len(pokemonScanResult["data"][0]) > 0:
            # Multiplier is used to "reset" the waiting time on stuck scanner
            # This prevents it from resetting non stop after it enters the condition once
            log_to_file("Pokemon scanning not progressing - Restarting", "ERROR")
            restart_run_docker_containers()

            channel = constants.CLIENT.get_channel(constants.MOD_CHANNEL_ID)
            execute_query_to_database(f"UPDATE pokestop SET last_updated = date_add(last_updated, INTERVAL {constants.TIME_MULTIPLIER * 30} MINUTE);")
            constants.TIME_MULTIPLIER = constants.TIME_MULTIPLIER + 1
            
            return True
        else:
            constants.TIME_MULTIPLIER = 1
    return False

async def is_quest_scanning_complete():
    get_current_quest_data()

    questScannerRunning = get_data_from_database("SELECT scanned FROM poliswag;", "poliswag")
    for questScannerRunningResult in questScannerRunning:
        questScannerRunning = questScannerRunningResult["data"][0]
        if questScannerRunning > 0:
            scanningCompletionStates = has_total_quests_scanned_been_reached()
            if scanningCompletionStates["Leiria"] and scanningCompletionStates["Marinha"]:
                set_quest_scanning_state()
                log_to_file(f"Pokestop quest scan completed")
                channel = constants.CLIENT.get_channel(constants.QUEST_CHANNEL_ID)
                await channel.send(embed=build_embed_object_title_description(
                    "SCAN DE QUESTS TERMINADO!", 
                    "Todas as quests do dia foram recolhidas e podem ser visualizadas com o uso de:\n!questleiria/questmarinha POKÉSTOP/QUEST/RECOMPENSA",
                    "Esta informação expira ao final do dia"
                    )
                )
                convivioChannel = constants.CLIENT.get_channel(constants.CONVIVIO_CHANNEL_ID)
                await retrieve_quest_summary(convivioChannel)

def has_total_quests_scanned_been_reached():
    lisbonTz = pytz.timezone('Europe/Lisbon')
    currentTime = datetime.datetime.now(lisbonTz)
    dstTimeChanges = 0
    if currentTime.dst() != datetime.timedelta(0):
        dstTimeChanges = 60

    questScanningStuck = get_data_from_database(f"SELECT IF(FROM_UNIXTIME(quest_timestamp) >= NOW() - INTERVAL {dstTimeChanges + 30} MINUTE, '', 'Stuck') AS status FROM trs_quest ORDER BY quest_timestamp DESC LIMIT 1;")
    status = [status["data"][0] for status in questScanningStuck]  # Extract status
    if 'Stuck' in status:
        log_to_file("Quest scanning not progressing - Restarting", "ERROR")
        restart_run_docker_containers()
        return {'Leiria': False, 'Marinha': False}

    
    totalPreviousScannedStops = get_data_from_database(f"SELECT pokestop_total_leiria, pokestop_total_marinha FROM poliswag;", "poliswag")
    totalPreviousScannedStopsLeiria = int(totalPreviousScannedStops[0]["data"][0])
    totalPreviousScannedStopsMarinhaGrande = int(totalPreviousScannedStops[0]["data"][1])

    totalScannedStops = get_data_from_database(f"SELECT CASE WHEN condition_alias = 'Longitude LIKE %-8.9%' THEN 'Marinha' WHEN condition_alias = 'Longitude NOT LIKE %-8.9%' THEN 'Leiria' END AS condition_alias, IFNULL(COUNT(pokestop.pokestop_id), 0) AS num_pokestops FROM (SELECT 'Longitude LIKE %-8.9%' AS condition_alias UNION ALL SELECT 'Longitude NOT LIKE %-8.9%') AS conditions LEFT JOIN trs_quest ON 1=1 LEFT JOIN pokestop ON trs_quest.GUID = pokestop.pokestop_id AND trs_quest.layer = 1 AND ((condition_alias = 'Longitude LIKE %-8.9%' AND pokestop.longitude LIKE '%-8.9%') OR (condition_alias = 'Longitude NOT LIKE %-8.9%' AND pokestop.longitude NOT LIKE '%-8.9%')) GROUP BY condition_alias;")
    if not totalScannedStops or len(totalScannedStops) < 2:
        return {'Leiria': False, 'Marinha': False}

    totalScannedStopsLeiria = next((item["data"][1] for item in totalScannedStops if item["data"][0] == 'Leiria'), 0)
    totalScannedStopsMarinhaGrande = next((item["data"][1] for item in totalScannedStops if item["data"][0] == 'Marinha'), 0)

    totalScannedStopsLeiria = int(totalScannedStopsLeiria)
    totalScannedStopsMarinhaGrande = int(totalScannedStopsMarinhaGrande)

    return {'Leiria': totalScannedStopsLeiria >= totalPreviousScannedStopsLeiria, 'Marinha': totalScannedStopsMarinhaGrande >= totalPreviousScannedStopsMarinhaGrande}
