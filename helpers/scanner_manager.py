import os, json, requests, datetime

import helpers.globals as globals
from helpers.utilities import build_query, build_embed_object_title_description, log_error
from helpers.notifications import fetch_new_pvp_data
from helpers.scanner_status import check_map_status
from helpers.data_quests_handler import fetch_today_data, verify_quest_scan_done, check_quest_scan_stuck

#await rename_voice_channel(message.content)
async def rename_voice_channel(name):
    await globals.CLIENT.get_channel(globals.VOICE_CHANNEL_ID).edit(name=name)

def start_pokestop_scan():
    set_quest_scanning_state(1)
    fetch_new_pvp_data()
    fetch_today_data()
    globals.DOCKER_CLIENT.restart(globals.ALARM_CONTAINER)
    globals.DOCKER_CLIENT.restart(globals.RUN_CONTAINER)

async def is_quest_scanning():
    try:
        execId = globals.DOCKER_CLIENT.exec_create(globals.DB_CONTAINER, build_query("SELECT scanned FROM poliswag WHERE scanned = 1;", "poliswag"))
        questResults = globals.DOCKER_CLIENT.exec_start(execId)
        if len(str(questResults).split("\\n")) > 1:
            if verify_quest_scan_done():
                set_quest_scanning_state()
                channel = globals.CLIENT.get_channel(globals.QUEST_CHANNEL_ID)
                await channel.send(embed=build_embed_object_title_description(
                    "SCAN DAS NOVAS QUESTS TERMINADO!", 
                    "Todas as informações relacionadas com as quests foram recolhidas e podem ser acedidas com o uso de:\n!questleiria/questmarinha POKÉSTOP/QUEST/RECOMPENSA",
                    "Esta informação só é válida até ao final do dia"
                    )
                )
            check_quest_scan_stuck()
        else:
            if datetime.datetime.now().day > globals.CURRENT_DAY:
                globals.CURRENT_DAY = datetime.datetime.now().day
                start_pokestop_scan()
            await check_map_status()
    except Exception as e:
        log_error("Error restarting container: " + str(e))

def set_quest_scanning_state(disabled = 0):
    execId = globals.DOCKER_CLIENT.exec_create(globals.DB_CONTAINER, build_query(f"UPDATE poliswag SET scanned = {disabled};", "poliswag"))
    globals.DOCKER_CLIENT.exec_start(execId)

def clear_old_pokestops_gyms():
    execId = globals.DOCKER_CLIENT.exec_create(globals.DB_CONTAINER, 
    build_query("DELETE FROM pokestop WHERE last_updated < (UNIX_TIMESTAMP() - 172800); DELETE FROM gym WHERE last_scanned < (UNIX_TIMESTAMP() - 172800);"))
    globals.DOCKER_CLIENT.exec_start(execId)
