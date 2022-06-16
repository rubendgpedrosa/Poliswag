import os, json, requests

import helpers.globals as globals
from helpers.quests import fetch_today_data

#await rename_voice_channel(message.content)
async def rename_voice_channel(name):
    await globals.CLIENT.get_channel(globals.VOICE_CHANNEL_ID).edit(name=name)

def start_pokestop_scan():
    execId = globals.DOCKER_CLIENT.exec_create(globals.DB_CONTAINER, 'mysql -uroot -pStrongPassword -D rocketdb -e "DELETE FROM pokemon WHERE disappear_time < DATE_SUB(NOW(), INTERVAL 48 HOUR); TRUNCATE TABLE trs_quest; TRUNCATE TABLE trs_visited;"')
    globals.DOCKER_CLIENT.exec_start(execId)

def check_quests_completed():
    fileTotal = get_file_total_quests()
    if fileTotal["totalQuestsLeiria"] == globals.LEIRIA_QUESTS_TOTAL and fileTotal["totalQuestsMarinha"] == globals.MARINHA_QUESTS_TOTAL:
        return {"leiria": False, "marinha": False}
    scannerTotal = get_scanner_total_quests()

    return {
        "leiria": (scannerTotal["totalQuestsLeiria"] == globals.LEIRIA_QUESTS_TOTAL and fileTotal["totalQuestsLeiria"] < scannerTotal["totalQuestsLeiria"]),
        "marinha": (scannerTotal["totalQuestsMarinha"] == globals.MARINHA_QUESTS_TOTAL and fileTotal["totalQuestsMarinha"] < scannerTotal["totalQuestsMarinha"])
    }

def start_pokemon_scan(new_walker_id, old_walker_id):
    globals.DOCKER_CLIENT.stop(globals.RUN_CONTAINER)
    globals.DOCKER_CLIENT.wait(globals.RUN_CONTAINER)
    execId = globals.DOCKER_CLIENT.exec_create(globals.DB_CONTAINER, f'mysql -uroot -pStrongPassword -D rocketdb -e "UPDATE settings_device SET walker_id = {new_walker_id} WHERE walker_id = {old_walker_id};"')
    globals.DOCKER_CLIENT.exec_start(execId)
    globals.DOCKER_CLIENT.start(globals.RUN_CONTAINER)


def get_scanner_total_quests():
    data = requests.get(globals.BACKEND_ENDPOINT + 'get_quests?fence=None')
    questText = data.text
    quests = json.loads(questText)
    os.remove(globals.QUESTS_FILE)

    with open(globals.QUESTS_FILE, 'w') as file:
        json.dump(quests, file, indent=4)
    return count_total_quests(quests)

def get_file_total_quests():
    with open(globals.QUESTS_FILE) as raw_data:
        quests = json.load(raw_data)
    return count_total_quests(quests)

def count_total_quests(quests):
    totalQuestsLeiria = 0
    totalQuestsMarinha = 0
    for quest in quests:
        if "-8.9" not in str(quest['longitude']):
            totalQuestsLeiria += 1
        else:
            totalQuestsMarinha += 1
    return {"totalQuestsLeiria": totalQuestsLeiria, "totalQuestsMarinha": totalQuestsMarinha}

def get_scan_status():
    execId = globals.DOCKER_CLIENT.exec_create(globals.DB_CONTAINER, f'mysql -uroot -pStrongPassword -D rocketdb -e "SELECT GUID, quest_timestamp FROM trs_quest WHERE quest_timestamp > (UNIX_TIMESTAMP() - 600);"')
    questResults = globals.DOCKER_CLIENT.exec_start(execId)
    return len(str(questResults).split("\\n")) == 1