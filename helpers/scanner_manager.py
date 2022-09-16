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
    fetch_new_pvp_data()
    fetch_today_data()
    constants.DOCKER_CLIENT.restart(constants.ALARM_CONTAINER)
    constants.DOCKER_CLIENT.restart(constants.RUN_CONTAINER)

def set_quest_scanning_state(disabled = 0):
    execId = constants.DOCKER_CLIENT.exec_create(constants.DB_CONTAINER, build_query(f"UPDATE poliswag SET scanned = {disabled};", "poliswag"))
    constants.DOCKER_CLIENT.exec_start(execId)
    log_error("set_quest_scanning_state state set to: " + str(disabled))

def truncate_quests_table():
    execId = constants.DOCKER_CLIENT.exec_create(constants.DB_CONTAINER, build_query(f"TRUNCATE TABLE trs_quest;"))
    constants.DOCKER_CLIENT.exec_start(execId)
    log_error("Truncated trs_quest table")

def clear_old_pokestops_gyms():
    execId = constants.DOCKER_CLIENT.exec_create(constants.DB_CONTAINER, 
    build_query("DELETE FROM pokestop WHERE last_updated < (NOW()-INTERVAL 3 DAY); DELETE FROM gym WHERE last_scanned < (NOW()-INTERVAL 3 DAY);"))
    constants.DOCKER_CLIENT.exec_start(execId)
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
