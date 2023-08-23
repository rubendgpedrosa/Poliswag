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
