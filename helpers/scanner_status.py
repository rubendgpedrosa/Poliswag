import helpers.constants as constants
import datetime, json

from helpers.scanner_manager import set_quest_scanning_state, rename_voice_channel, start_pokestop_scan, clear_old_pokestops_gyms, restart_run_docker_containers, run_database_query
from helpers.utilities import  build_embed_object_title_description, log_error, build_embed_object_title_description

boxUsersData = [
    {"owner": "Faynn", "boxes": ["Tx9s1", "a95xF1"], "mention": "98846248865398784"},
    {"owner": "JMBoy", "boxes": ["Tx9s1_JMBoy", "Tx9s2_JMBoy", "Tx9s3_JMBoy"], "mention": "308000681271492610"},
    {"owner": "Ethix", "boxes": ["Tx9s1_Ethix"], "mention": "313738342904627200"},
    {"owner": "Anakin", "boxes": ["Tx9s1_Anakin"], "mention": "339466204638871552"}
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
            if box == "PoGoLeiria":
                box = "Tx9s3_JMBoy"
            # for boxuser in boxUsersData:
            #     if box in boxuser["boxes"]:
            #         user = globals.CLIENT.fetch_user(boxuser["mention"])
            #         await globals.CLIENT.send(user, "Box " + box + " precisa ser reiniciada.")
        await rename_voice_channel(len(listBoxStatusResults))
        return
    await rename_voice_channel(0)

async def check_map_status():
    #70mins since the mysql timezone and vps timezone have an hour differente. 60mins + 30mins
    pokemonScanResults = run_database_query("SELECT pokestop_id FROM pokestop WHERE last_updated > NOW() - INTERVAL 90 MINUTE ORDER BY last_updated DESC LIMIT 1;")
    if len(str(pokemonScanResults).split("\\n")) == 1:
        log_error("Restarting MAD instance since scanner has no new spawns for 30mins")
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
    try:
        questResults = run_database_query("SELECT scanned FROM poliswag WHERE scanned = 1;", "poliswag")
        if len(str(questResults).split("\\n")) > 1:
            if verify_quest_scan_done():
                set_quest_scanning_state()
                channel = constants.CLIENT.get_channel(constants.QUEST_CHANNEL_ID)
                await channel.send(embed=build_embed_object_title_description(
                    "SCAN DAS NOVAS QUESTS TERMINADO!", 
                    "Todas as informações relacionadas com as quests foram recolhidas e podem ser acedidas com o uso de:\n!questleiria/questmarinha POKÉSTOP/QUEST/RECOMPENSA",
                    "Esta informação só é válida até ao final do dia"
                    )
                )
            check_quest_scan_stuck()
        else:
            if datetime.datetime.now().day > constants.CURRENT_DAY:
                constants.CURRENT_DAY = datetime.datetime.now().day
                log_error("Pokestop scanning initialized")
                start_pokestop_scan()
                clear_old_pokestops_gyms()
            await check_map_status()
    except Exception as e:
        log_error("is_quest_scanning: " + str(e))

def verify_quest_scan_done():
    with open(constants.QUESTS_FILE) as raw_data:
        jsonPokemonData = json.load(raw_data)
    log_error("verify_quest_scan_done: " + str(len(jsonPokemonData) >= 360))
    return len(jsonPokemonData) >= 360

def check_quest_scan_stuck():
    # Always add an hour due to timezone
    questsWhereRecentlyScanned = run_database_query("select GUID, quest_timestamp from trs_quest WHERE quest_timestamp > (UNIX_TIMESTAMP() - 3900);")
    if len(str(questsWhereRecentlyScanned).split("\\n")) == 1:
        restart_run_docker_containers()
        log_error("Restarting container since quest scanning not progressing")
