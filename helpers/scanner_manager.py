import helpers.constants as constants

from helpers.utilities import log_to_file, time_now, clear_quest_file, build_embed_object_title_description
from helpers.database_connector import execute_query_to_database, get_data_from_database
from helpers.poliswag import fetch_new_pvp_data

def start_pokestop_scan():
    clear_quest_file()
    clear_old_pokestops_gyms()
    clear_quests_table()
    
    fetch_new_pvp_data()
    
    set_last_scanned_date(time_now())
    set_quest_scanning_state(1)
    
    restart_alarm_docker_container()
    restart_run_docker_containers()

def set_quest_scanning_state(state = 0):
    execute_query_to_database(f"UPDATE poliswag SET scanned = '{state}';", "poliswag")
    log_to_file(f"{'Disabled' if state == 0 else 'Enabled'} quest scanning mode")

def clear_quests_table():
    execute_query_to_database("TRUNCATE TABLE trs_quest;")
    log_to_file("Truncated quests table sucessfully!")

def clear_old_pokestops_gyms():
    execute_query_to_database("DELETE FROM pokestop WHERE last_updated < (NOW()-INTERVAL 3 DAY);")
    log_to_file("Expired pokestops and gyms cleared sucessfully!")
    
def set_last_scanned_date(lastScannedDate):
    execute_query_to_database(f"UPDATE poliswag SET last_scanned_date = '{lastScannedDate}'", "poliswag")
    log_to_file(f"New last_scanned_date set to {lastScannedDate}")

async def rename_voice_channel(totalBoxesFailing):
    message = "STATUS: 🟢"
    if totalBoxesFailing > 0 and totalBoxesFailing < 3:
        message = "STATUS: 🟡"
    if totalBoxesFailing > 2 and totalBoxesFailing < 7:
        message = "STATUS: 🟠"
    if totalBoxesFailing == 7:
        message = "STATUS: 🔴"
    voiceChannel = constants.CLIENT.get_channel(constants.VOICE_CHANNEL_ID)
    if voiceChannel.name != message:
        log_to_file(f"Number of devices encountering issues: {totalBoxesFailing}")
        await voiceChannel.edit(name=message)

def restart_run_docker_containers():
    constants.DOCKER_CLIENT_API.restart(constants.RUN_CONTAINER)

def restart_alarm_docker_container():
    constants.DOCKER_CLIENT_API.restart(constants.ALARM_CONTAINER)

async def start_quest_scanner_if_day_change():
    didDayChangeFromStoredDb = get_data_from_database(f"SELECT last_scanned_date FROM poliswag WHERE last_scanned_date < '{time_now()}' OR last_scanned_date IS NULL;", "poliswag")
    if len(didDayChangeFromStoredDb) > 0:
        log_to_file("Day change encountered, pokestop scanning initialized")
        start_pokestop_scan()
        questChannel = constants.CLIENT.get_channel(constants.QUEST_CHANNEL_ID)
        await questChannel.send(embed=build_embed_object_title_description("Mudança de dia detetada", "Scan das novas quests inicializado!"))
        log_to_file("Pokestop quest scanning started")
