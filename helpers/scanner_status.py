import helpers.constants as constants
import time
import discord

from helpers.utilities import build_embed_object_title_description, log_to_file, build_embed_object_title_description, clean_map_stats_channel
from helpers.scanner_manager import set_quest_scanning_state, restart_run_docker_containers
from helpers.database_connector import execute_query_to_database, get_data_from_database
from helpers.quests import get_current_quest_data

async def check_boxes_with_issues():
    dstTimeChanges = 0
    # Check if summer time is active
    if time.daylight == 1:
        dstTimeChanges = 3600
    listBoxStatusResults = get_data_from_database(f"SELECT settings_device.name AS name FROM trs_status LEFT JOIN settings_device ON trs_status.device_id = settings_device.device_id WHERE trs_status.device_id < 14 AND (TIMESTAMPDIFF(SECOND, trs_status.lastProtoDateTime, NOW()) > {dstTimeChanges + 1200} OR trs_status.lastProtoDateTime IS NULL);")
    if len(listBoxStatusResults) > 0:
        await notify_devices_down(listBoxStatusResults)
        await rename_voice_channels(listBoxStatusResults)
    else:
        await clean_map_stats_channel("clear")
        await rename_voice_channels([])

    
async def notify_devices_down(totalBoxesFailing):
    boxUsersMapping = {
        "Tx9s": "<@98846248865398784>",
        "a95xF1": "<@98846248865398784>",
        "PoGoLeiria": "<@308000681271492610>",
        "Tx9s1_JMBoy": "<@308000681271492610>",
        "Tx9s2_JMBoy": "<@308000681271492610>",
        "Tx9s1_Ethix": "<@313738342904627200>",
        "Tx9s1_Anakin": "<@339466204638871552>"
    }

    usersToNotify = []
    boxNamesToNotify = []
    for boxName in totalBoxesFailing:
        boxName = boxName["data"][0]
        userMention = boxUsersMapping.get(boxName)
        renamedBoxName = rename_box(boxName)
        if userMention:
            usersToNotify.append(userMention)
            boxNamesToNotify.append(renamedBoxName)

    usersToNotifyString = " ".join(usersToNotify)
    boxesToNotifyString = ", ".join(boxNamesToNotify) + f" precisa{'' if len(boxNamesToNotify) == 1 else 'm'} de um reboot"

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
        if downCounter == 0:
            return f"{region}: 🟢"
        elif downCounter in [1, 2]:
            return f"{region}: 🟡"
        elif downCounter in [3, 4]:
            return f"{region}: 🟠"
        elif downCounter >= 5:
            return f"{region}: 🔴"
    elif region == "MARINHA":
        if downCounter == 0:
            return f"{region}: 🟢"
        elif downCounter == 1:
            return f"{region}: 🟡"
        elif downCounter >= 2:
            return f"{region}: 🔴"
    return f"{region}: 🟢"

def rename_box(boxName):
    if boxName == "PoGoLeiria":
        return "Tx9s2_JMBoy"
    elif boxName == "Tx9s2_JMBoy":
        return "Tx9s3_JMBoy"
    else:
        return boxName

def split_list_by_region(boxName):
    if boxName == "Tx9s1_Ethix" or boxName == "Tx9s1_Anakin":
        return "MarinhaGrande"
    else:
        return "Leiria"

async def restart_map_container_if_scanning_stuck():
    dstTimeChanges = 0
    # Check if summer time is active
    if time.daylight == 1:
        dstTimeChanges = 60
    pokemonScanResults = get_data_from_database(f"SELECT IF(last_updated >= NOW() - INTERVAL {constants.TIME_MULTIPLIER * (dstTimeChanges + 30)} MINUTE, '', 'Stuck') AS status FROM pokestop ORDER BY last_updated DESC LIMIT 1;")
    for pokemonScanResult in pokemonScanResults:
        if len(pokemonScanResult["data"][0]) > 0:
            # Multiplier is used to "reset" the waiting time on stuck scanner
            # This prevents it from resetting non stop after it enters the condition once
            log_to_file("Pokemon scanning not progressing - Restarting")
            restart_run_docker_containers()

            channel = constants.CLIENT.get_channel(constants.MOD_CHANNEL_ID)
            await channel.send(embed=build_embed_object_title_description(
                "ANOMALIA DETECTADA!", 
                "Reboot efetuado para corrigir anomalia na mapa"
                ),
                delete_after=30
            )
            execute_query_to_database(f"UPDATE pokestop SET last_updated = date_add(last_updated, INTERVAL {constants.TIME_MULTIPLIER * 30} MINUTE);")
            constants.TIME_MULTIPLIER = constants.TIME_MULTIPLIER * 2
            
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
                #embedObjects = build_quest_summary_embed_objects(retrieve_sort_quest_data())
                #await channel.send(content=f"**RESUMO QUESTS LEIRIA**\n{embedObjects['Leiria']}")
                #await channel.send(content=f"**RESUMO QUESTS MARINHA GRANDE**\n{embedObjects['MarinhaGrande']}")
                await channel.send(embed=build_embed_object_title_description(
                    "SCAN DE QUESTS TERMINADO!", 
                    "Todas as quests do dia foram recolhidas e podem ser visualizadas com o uso de:\n!questleiria/questmarinha POKÉSTOP/QUEST/RECOMPENSA",
                    "Esta informação expira ao final do dia"
                    )
                )

def has_total_quests_scanned_been_reached():
    totalPreviousScannedStops = get_data_from_database(f"SELECT pokestop_total_leiria, pokestop_total_marinha FROM poliswag;", "poliswag")
    totalPreviousScannedStopsLeiria = int(totalPreviousScannedStops[0]["data"][0])
    totalPreviousScannedStopsMarinhaGrande = int(totalPreviousScannedStops[0]["data"][1])

    totalScannedStops = get_data_from_database(f"SELECT COUNT(pokestop.pokestop_id) AS num_pokestops FROM trs_quest LEFT JOIN pokestop ON trs_quest.GUID = pokestop.pokestop_id WHERE trs_quest.layer = 1 GROUP BY IF(pokestop.longitude NOT LIKE '%-8.9%', 'Longitude not like %-8.9%', 'Longitude like %-8.9%');")

    if not totalScannedStops or len(totalScannedStops) < 2:
        return {'Leiria': False, 'Marinha': False}
    
    totalScannedStopsLeiria = int(totalScannedStops[1]["data"][0])
    totalScannedStopsMarinhaGrande = int(totalScannedStops[0]["data"][0])

    return {'Leiria': totalScannedStopsLeiria >= totalPreviousScannedStopsLeiria, 'Marinha': totalScannedStopsMarinhaGrande >= totalPreviousScannedStopsMarinhaGrande}
