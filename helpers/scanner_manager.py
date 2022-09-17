import helpers.constants as constants

from helpers.quests import fetch_today_data
from helpers.poliswag import fetch_new_pvp_data
from helpers.utilities import build_query, log_error

#await rename_voice_channel(message.content)
async def rename_voice_channel(name):
    await constants.CLIENT.get_channel(constants.VOICE_CHANNEL_ID).edit(name=name)

def start_pokestop_scan():
    truncate_quests_table()
    set_quest_scanning_state(1)
    restart_alarm_docker_container()
    restart_run_docker_containers()
    fetch_new_pvp_data()
    fetch_today_data()

def set_quest_scanning_state(disabled = 0):
    run_database_query("UPDATE poliswag SET scanned = {disabled};", "poliswag")
    log_error("set_quest_scanning_state state set to: " + str(disabled))

def truncate_quests_table():
    run_database_query("TRUNCATE TABLE trs_quest;")
    log_error("Truncated trs_quest table")

def clear_old_pokestops_gyms():
    run_database_query("DELETE FROM pokestop WHERE last_updated < (NOW()-INTERVAL 3 DAY); DELETE FROM gym WHERE last_scanned < (NOW()-INTERVAL 3 DAY);")
    log_error("Clearing expired pokestops and gyms")

async def rename_voice_channel(totalBoxesFailing):
    message = "SCANNER: 🟢"
    if totalBoxesFailing > 0 and totalBoxesFailing < 3:
        message = "SCANNER: 🟡"
    if totalBoxesFailing > 2 and totalBoxesFailing < 7:
        message = "SCANNER: 🟠"
    if totalBoxesFailing == 7:
        message = "SCANNER: 🔴"
    voiceChannel = constants.CLIENT.get_channel(constants.VOICE_CHANNEL_ID)
    if voiceChannel.name != message:
        await voiceChannel.edit(name=message)

def restart_run_docker_containers():
    constants.DOCKER_CLIENT.restart(constants.RUN_CONTAINER)

def restart_alarm_docker_container():
    constants.DOCKER_CLIENT.restart(constants.ALARM_CONTAINER)

def run_database_query(query, database = None):
    execId = constants.DOCKER_CLIENT.exec_create(constants.DB_CONTAINER, build_query(query, database))
    return constants.DOCKER_CLIENT.exec_start(execId)