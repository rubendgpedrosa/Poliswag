import os, json, requests

import helpers.globals as globals
from helpers.quests import fetch_today_data

#await rename_voice_channel(message.content)
async def rename_voice_channel(name):
    await globals.CLIENT.get_channel(globals.VOICE_CHANNEL_ID).edit(name=name)

def start_pokestop_scan():
    open(globals.QUESTS_FILE, "w").close()
    open(globals.SCANNED_FILE, "w").close()
    execId = globals.DOCKER_CLIENT.exec_create(globals.DB_CONTAINER, 'mysql -uroot -pStrongPassword -D rocketdb -e "DELETE FROM pokemon WHERE disappear_time < DATE_SUB(NOW(), INTERVAL 48 HOUR); TRUNCATE TABLE trs_quest; TRUNCATE TABLE trs_visited;"')
    globals.DOCKER_CLIENT.exec_start(execId)
    globals.DOCKER.restart(globals.RUN_CONTAINER)
    globals.DOCKER.restart(globals.REDIS_CONTAINER)

def get_scan_status():
    execId = globals.DOCKER_CLIENT.exec_create(globals.DB_CONTAINER, f'mysql -uroot -pStrongPassword -D rocketdb -e "SELECT GUID, quest_timestamp FROM trs_quest WHERE quest_timestamp > (UNIX_TIMESTAMP() - 600);"')
    questResults = globals.DOCKER_CLIENT.exec_start(execId)
    return len(str(questResults).split("\\n")) == 1

def clear_old_pokestops_gyms():
    execId = globals.DOCKER_CLIENT.exec_create(globals.DB_CONTAINER, 
    'mysql -uroot -pStrongPassword -D rocketdb -e "DELETE FROM pokestop WHERE last_updated < (UNIX_TIMESTAMP() - 172800); DELETE FROM gym WHERE last_scanned < (UNIX_TIMESTAMP() - 172800);"')
    globals.DOCKER_CLIENT.exec_start(execId)