import helpers.constants as constants

from helpers.scanner_manager import set_quest_scanning_state, rename_voice_channel, restart_run_docker_containers
from helpers.utilities import build_embed_object_title_description, log_to_file, build_embed_object_title_description, run_database_query, get_data_from_database, build_notification_mention_string, build_and_send_embed_object_notify_box_status
from helpers.quests import get_current_quest_data

async def check_boxes_with_issues():
    boxStatusResults = get_data_from_database("SELECT GROUP_CONCAT(settings_device.name SEPARATOR '#') AS names FROM trs_status LEFT JOIN settings_device ON trs_status.device_id = settings_device.device_id WHERE trs_status.device_id < 14 AND (TIMESTAMPDIFF(SECOND, trs_status.lastProtoDateTime, NOW()) > 1200 OR trs_status.lastProtoDateTime IS NULL);")
    listBoxStatusResults = []
    if boxStatusResults != "NULL":
        listBoxStatusResults = boxStatusResults.split('#')

    if len(listBoxStatusResults) > 0:
        for box in listBoxStatusResults:
            # Edge case where we replace these values since they are different in the DB
            if box.lower() == "pogoleiria":
                box = "Tx9s2_JMBoy"
            if box.lower() == "tx9s2_jmboy":
                box = "Tx9s3_JMBoy"
        await rename_voice_channel(len(listBoxStatusResults))
    else:
        await rename_voice_channel(0)

async def restart_map_container_if_scanning_stuck():
    pokemonScanResults = get_data_from_database(f"SELECT IF(last_updated >= NOW() - INTERVAL {constants.TIME_MULTIPLIER * 30} MINUTE, '', 'Stuck') AS status FROM pokestop ORDER BY last_updated DESC LIMIT 1;")
    if pokemonScanResults != "":
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
        run_database_query(f"UPDATE pokestop SET last_updated = date_add(last_updated, INTERVAL {constants.TIME_MULTIPLIER * 30} MINUTE);")
        constants.TIME_MULTIPLIER = constants.TIME_MULTIPLIER * 2
        
        return True
    else:
        constants.TIME_MULTIPLIER = 1
    return False

async def is_quest_scanning_complete():
    get_current_quest_data()

    questScannerRunning = int(get_data_from_database("SELECT scanned FROM poliswag;", "poliswag"))
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

def has_total_quests_scanned_been_reached():
    totalPreviousScannedStops = int(get_data_from_database(f"SELECT pokestop_total_leiria + pokestop_total_marinha AS total_sum FROM poliswag;", "poliswag"))
    totalScannedStops = int(get_data_from_database(f"SELECT COUNT(GUID) AS totalPokestops FROM trs_quest;"))
    return totalScannedStops >= totalPreviousScannedStops

async def notify_users_devices_need_restart(notificationMentionString, listBoxStatusResults):
    channel = constants.CLIENT.get_channel(constants.MAPSTATS_CHANNEL_ID)
    await build_and_send_embed_object_notify_box_status(channel, notificationMentionString, listBoxStatusResults)
