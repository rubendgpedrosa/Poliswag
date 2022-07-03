import os, json, requests

import helpers.globals as globals
from helpers.utilities import build_query
from helpers.notifications import fetch_new_pvp_data
from helpers.data_quests_handler import fetch_today_data, verify_quest_scan_done

#await rename_voice_channel(message.content)
async def rename_voice_channel(name):
    await globals.CLIENT.get_channel(globals.VOICE_CHANNEL_ID).edit(name=name)

def start_pokestop_scan():
    fetch_new_pvp_data()
    fetch_today_data()
    set_quest_scanning_state(1)
    globals.DOCKER_CLIENT.restart(globals.ALARM_CONTAINER)

def is_quest_scanning():
    execId = globals.DOCKER_CLIENT.exec_create(globals.DB_CONTAINER, build_query("SELECT scanned FROM poliswag WHERE scanned = 1;"))
    questResults = globals.DOCKER_CLIENT.exec_start(execId)
    return len(str(questResults).split("\\n")) > 1

def set_quest_scanning_state(disabled = 0):
    execId = globals.DOCKER_CLIENT.exec_create(globals.DB_CONTAINER, build_query(f"UPDATE poliswag SET scanned = {disabled};", "poliswag"))
    globals.DOCKER_CLIENT.exec_start(execId)

def clear_old_pokestops_gyms():
    execId = globals.DOCKER_CLIENT.exec_create(globals.DB_CONTAINER, 
    build_query("DELETE FROM pokestop WHERE last_updated < (UNIX_TIMESTAMP() - 172800); DELETE FROM gym WHERE last_scanned < (UNIX_TIMESTAMP() - 172800);"))
    globals.DOCKER_CLIENT.exec_start(execId)
