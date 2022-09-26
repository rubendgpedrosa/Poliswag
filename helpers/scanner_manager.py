import helpers.constants as constants

from helpers.utilities import log_to_file, run_database_query

def start_pokestop_scan():
    truncate_quests_table()
    set_quest_scanning_state(1)
    restart_alarm_docker_container()
    restart_run_docker_containers()

def set_quest_scanning_state(state = 0):
    run_database_query(f"UPDATE poliswag SET scanned = {state};", "poliswag")
    log_to_file(f"{'Disabled' if state == 0 else 'Enabled'} quest scanning mode")

def truncate_quests_table():
    run_database_query("TRUNCATE TABLE trs_quest;")
    log_to_file("Truncated trs_quest table")

def clear_old_pokestops_gyms():
    run_database_query("DELETE FROM pokestop WHERE last_updated < (NOW()-INTERVAL 3 DAY); DELETE FROM gym WHERE last_scanned < (NOW()-INTERVAL 3 DAY);")
    log_to_file("Clearing expired pokestops and gyms")

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
        log_to_file(f"Number of devices encountering issues: {totalBoxesFailing}")
        await voiceChannel.edit(name=message)

def restart_run_docker_containers():
    constants.DOCKER_CLIENT.restart(constants.RUN_CONTAINER)

def restart_alarm_docker_container():
    constants.DOCKER_CLIENT.restart(constants.ALARM_CONTAINER)
