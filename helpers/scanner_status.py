import helpers.constants as constants
import datetime, json

from helpers.scanner_manager import set_quest_scanning_state, rename_voice_channel, start_pokestop_scan, clear_old_pokestops_gyms, restart_run_docker_containers
from helpers.utilities import  clear_quest_file, build_embed_object_title_description, log_to_file, build_embed_object_title_description, did_day_change, private_message_user_by_id, run_database_query
from helpers.poliswag import fetch_new_pvp_data
from helpers.quests import fetch_today_data

boxUsersData = [
    {"owner": "Faynn", "boxes": ["Tx9s1", "a95xF1"], "mention": "98846248865398784"},
    {"owner": "JMBoy", "boxes": ["Tx9s1_JMBoy", "Tx9s2_JMBoy", "Tx9s3_JMBoy"], "mention": "98846248865398784"},
    {"owner": "Ethix", "boxes": ["Tx9s1_Ethix"], "mention": "98846248865398784"},
    {"owner": "Anakin", "boxes": ["Tx9s1_Anakin"], "mention": "98846248865398784"}
]

async def check_boxes_issues():
    # 4500 since it's 900 seconds + 1 hour (vps timezone differences)
    boxStatusResults = run_database_query("SELECT settings_device.name FROM trs_status LEFT JOIN settings_device ON trs_status.device_id = settings_device.device_id WHERE trs_status.device_id < 14 AND TIMESTAMPDIFF(SECOND, trs_status.lastProtoDateTime, NOW()) > 4500;")
    listBoxStatusResults = str(boxStatusResults).split("\\n")
    # Remove first and last element
    del listBoxStatusResults[0]
    if len(listBoxStatusResults) > 0:
        del listBoxStatusResults[len(listBoxStatusResults) - 1]
    if listBoxStatusResults is not None and len(listBoxStatusResults) > 0:
        for box in listBoxStatusResults:
            # Edge case where we replace this value since it's different in the db
            if box.lower() == "pogoleiria":
                box = "Tx9s3_JMBoy"
            # for boxUser in boxUsersData:
            #     if box in boxUser["boxes"]:
            #         await private_message_user_by_id(boxUser["mention"], build_embed_object_title_description("Box " + boxToRestart + " precisa ser reiniciada."))
        await rename_voice_channel(len(listBoxStatusResults))
    else:
        await rename_voice_channel(0)

async def check_map_status():
    #70mins since the mysql timezone and vps timezone have an hour differente. 60mins + 30mins
    pokemonScanResults = run_database_query("SELECT pokestop_id FROM pokestop WHERE last_updated > NOW() - INTERVAL 90 MINUTE ORDER BY last_updated DESC LIMIT 1;")
    if len(str(pokemonScanResults).split("\\n")) == 1:
        log_to_file("Pokemon scanning not progressing - Restarting")
        channel = constants.CLIENT.get_channel(constants.MOD_CHANNEL_ID)
        await channel.send(embed=build_embed_object_title_description(
            "ANOMALIA DETECTADA!", 
            "Reboot efetuado para corrigir anomalia na mapa"
            ),
            delete_after=30
        )
        run_database_query("UPDATE pokestop SET last_updated = date_add(last_updated, INTERVAL 30 MINUTE);")
        restart_run_docker_containers()

async def is_quest_scanning():
    questResults = run_database_query("SELECT scanned FROM poliswag WHERE scanned = 1;", "poliswag")
    if len(str(questResults).split("\\n")) > 1:
        if verify_quest_scan_done():
            log_to_file(f"Pokestop scan completed")
            set_quest_scanning_state()
            channel = constants.CLIENT.get_channel(constants.QUEST_CHANNEL_ID)
            await channel.send(embed=build_embed_object_title_description(
                "SCAN DAS NOVAS QUESTS TERMINADO!", 
                "Todas as informações relacionadas com as quests foram recolhidas e podem ser acedidas com o uso de:\n!questleiria/questmarinha POKÉSTOP/QUEST/RECOMPENSA",
                "Esta informação só é válida até ao final do dia"
                )
            )
        else:
            fetch_today_data()
        check_quest_scan_stuck()
    else:
        if did_day_change():
            log_to_file("Pokestop scanning initialized")
            clear_quest_file()
            start_pokestop_scan()
            clear_old_pokestops_gyms()
            fetch_new_pvp_data()
        else:
            fetch_today_data()
        await check_map_status()

def verify_quest_scan_done():
    with open(constants.QUESTS_FILE) as raw_data:
        jsonPokemonData = json.load(raw_data)
    return len(jsonPokemonData) >= 365

# Multiplier exists, because updating the column related top quest_timestamp makes scanning reset. So instead, we add and substract a TIME_MULTIPLIER in order to "reset" the time
def check_quest_scan_stuck():
    # Always add an hour due to timezone
    questsWhereRecentlyScanned = run_database_query(f"select GUID, quest_timestamp from trs_quest WHERE quest_timestamp > (UNIX_TIMESTAMP() - {constants.TIME_MULTIPLIER * 3300}) ORDER BY quest_timestamp DESC LIMIT 1;")
    if len(str(questsWhereRecentlyScanned).split("\\n")) == 1:
        restart_run_docker_containers()
        constants.TIME_MULTIPLIER = 2
        log_to_file("Quest scanning not progressing - Restarting")
    else:
        constants.TIME_MULTIPLIER = 1
