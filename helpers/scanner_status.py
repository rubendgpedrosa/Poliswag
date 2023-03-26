import helpers.constants as constants
import time

from helpers.utilities import build_embed_object_title_description, log_to_file, build_embed_object_title_description
from helpers.scanner_manager import set_quest_scanning_state, rename_voice_channel, restart_run_docker_containers
from helpers.database_connector import execute_query_to_database, get_data_from_database
from helpers.quests import get_current_quest_data

async def check_boxes_with_issues():
    dstTimeChanges = 0
    # Check if summer time is active
    if time.daylight == 1:
        dstTimeChanges = 3600
    listBoxStatusResults = get_data_from_database(f"SELECT settings_device.name AS name FROM trs_status LEFT JOIN settings_device ON trs_status.device_id = settings_device.device_id WHERE trs_status.device_id < 14 AND (TIMESTAMPDIFF(SECOND, trs_status.lastProtoDateTime, NOW()) > {dstTimeChanges + 1200} OR trs_status.lastProtoDateTime IS NULL);")
    if len(listBoxStatusResults) > 0:
        await rename_voice_channel(len(listBoxStatusResults))
    else:
        await rename_voice_channel(0)

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
            if has_total_quests_scanned_been_reached():
                log_to_file(f"Pokestop quest scan completed")
                set_quest_scanning_state()
                channel = constants.CLIENT.get_channel(constants.QUEST_CHANNEL_ID)
                await channel.send(embed=build_embed_object_title_description(
                    "SCAN DAS NOVAS QUESTS TERMINADO!", 
                    "Todas as informações relacionadas com as quests foram recolhidas e podem ser acedidas com o uso de:\n!questleiria/questmarinha POKÉSTOP/QUEST/RECOMPENSA",
                    "Esta informação só é válida até ao final do dia"
                    )
                )

def has_total_quests_scanned_been_reached(targetColumn = "pokestop_total_leiria", longitudeLeiria = "NOT "):
    totalPreviousScannedStops = get_data_from_database(f"SELECT {targetColumn} FROM poliswag;", "poliswag")
    for totalPreviousScannedStopsResult in totalPreviousScannedStops:
        totalPreviousScannedStops = int(totalPreviousScannedStopsResult["data"][0])
    totalScannedStops = get_data_from_database(f"SELECT COUNT(GUID) FROM trs_quest LEFT JOIN pokestop ON pokestop.pokestop_id = trs_quest.GUID WHERE pokestop.longitude {longitudeLeiria} LIKE '%-8.9%' AND layer = 1;")
    for totalScannedStopsResult in totalScannedStops:
        totalScannedStops = int(totalScannedStopsResult["data"][0])
    return totalScannedStops >= totalPreviousScannedStops
