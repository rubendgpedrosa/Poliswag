import helpers.constants as constants
import discord
import datetime, requests
import pytz

from helpers.utilities import build_embed_object_title_description, log_to_file, build_embed_object_title_description, clean_map_stats_channel, is_there_message_to_be_deleted
from helpers.scanner_manager import set_quest_scanning_state, restart_run_docker_containers
from helpers.database_connector import get_data_from_database
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

async def check_boxes_with_issues():
    deviceStatusJson = await get_devices_status()
    if not deviceStatusJson:
        return
    devicesStatusList = prepare_dashboard_data(deviceStatusJson)
    await device_status_embed_dashboard(devicesStatusList)
    usersDeviceMap = map_users_failing_devices(devicesStatusList)
    await notify_users_failing_devices(usersDeviceMap)
    await rename_voice_channels(devicesStatusList)

async def get_devices_status():
    try:
        data = requests.get(constants.BACKEND_ENDPOINT + 'get_status', timeout=20)
        data.raise_for_status() # Raise an exception if there is an HTTP error status code (4xx or 5xx)
        if data.status_code == 200:
            return data.json()
        return None
    except (requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
        print(f"Error: {e}")
        return None

def prepare_dashboard_data(deviceStatus):
    devicesStatusList = []
    for device in deviceStatus:
        # Check if connected to MAD
        deviceNameStatus = f"{get_device_status_icon(device['lastProtoDateTime'])} {device['name']} - {get_device_connected_status(device)}"
        devicesStatusList.append(deviceNameStatus)
    return sorted(devicesStatusList, key=lambda x: x.split(" - ")[0].split(" ")[1])
        
def get_device_status_icon(deviceTimeStamp):
    if datetime.datetime.now() - datetime.datetime.fromtimestamp(deviceTimeStamp) > datetime.timedelta(minutes=25):
        return "🔴"
    if datetime.datetime.now() - datetime.datetime.fromtimestamp(deviceTimeStamp) > datetime.timedelta(minutes=20):
        return "🟠"
    if datetime.datetime.now() - datetime.datetime.fromtimestamp(deviceTimeStamp) > datetime.timedelta(minutes=15):
        return "🟡"
    return "🟢"

def get_device_connected_status(device):
    twentyMinutesAgo = datetime.datetime.now() - datetime.timedelta(minutes=20)
    if device["lastProtoDateTime"] <= twentyMinutesAgo.timestamp() and \
       (device["lastPogoReboot"] == 0 or device["lastPogoReboot"] <= twentyMinutesAgo.timestamp()):
        return "**DISCONNECTED**"
    return "Connected"

async def device_status_embed_dashboard(devicesStatusList):
    mapStatsChannel = constants.CLIENT.get_channel(constants.MAPSTATS_CHANNEL_ID)
    embedMessage = await mapStatsChannel.fetch_message(constants.POLISWAG_MESSAGE_ID)
    
    nbActiveDevices = len([device for device in devicesStatusList if "🟢" in device])
    embed = discord.Embed(
        color=0x7b83b4
    )
    
    leiriaDevices = [device for device in devicesStatusList if "Tx9s_" not in device]
    marinhaDevices = [device for device in devicesStatusList if "Tx9s_" in device]

    embed.add_field(name="LEIRIA:", value='\n\n'.join(leiriaDevices) + "\n\u200b", inline=False)
    embed.add_field(name="MARINHA GRANDE:", value='\n\n'.join(marinhaDevices) + "\n\u200b", inline=False)
    embed.set_footer(text=f"Última atualização: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    await embedMessage.edit(content=f"**DISPOSITIVOS ATIVOS: {nbActiveDevices} (em {len(devicesStatusList)})\n**", embed=embed)
    
def map_users_failing_devices(devicesStatusList):
    userDevicesMap = []
    for device in devicesStatusList:
        if "DISCONNECTED" in device:
            deviceName = device.split(" - ")[0].split()[1]  # Extracting the second element after splitting by spaces which contains the device name
            userMention = boxUsersMapping.get(deviceName)
            if userMention:
                if userMention not in userDevicesMap:
                    userDevicesMap.append(userMention)
    return userDevicesMap

async def notify_users_failing_devices(usersDeviceMap):
    userMentionString = ",".join(usersDeviceMap)
    if not userMentionString:
        await clean_map_stats_channel("clear")
        return

    if await is_there_message_to_be_deleted(userMentionString):
        await clean_map_stats_channel("clear")
        mapStatusChannel = constants.CLIENT.get_channel(constants.MAPSTATS_CHANNEL_ID)
        await mapStatusChannel.send(content=userMentionString)

async def rename_voice_channels(devicesStatusList):
    leiriaVoiceChannel = constants.CLIENT.get_channel(constants.VOICE_CHANNEL_ID)
    marinhaVoiceChannel = constants.CLIENT.get_channel(constants.VOICE_CHANNEL_MARINHA_ID)

    leiriaDownCounter = sum("Leiria" in split_list_by_region(boxName) and "🟢" not in boxName for boxName in devicesStatusList)
    marinhaDownCounter = sum("MarinhaGrande" in split_list_by_region(boxName) and "🟢" not in boxName for boxName in devicesStatusList)

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
    if "Tx9s_Ethix" in boxName or "Tx9s_Anakin" in boxName:
        return "MarinhaGrande"
    else:
        return "Leiria"

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
