import os, json, requests

import helpers.globals as globals
from helpers.quests import fetch_today_data

#await rename_voice_channel(message.content)
async def rename_voice_channel(name):
    await globals.CLIENT.get_channel(globals.VOICE_CHANNEL_ID).edit(name=name)

def start_pokestop_scan():
    fetch_today_data()
    os.system('docker stop pokemon_mad')
    os.system('docker exec -i pokemon_rocketdb mysql -uroot -pStrongPassword  <<< "use rocketdb; DELETE FROM pokemon WHERE disappear_time < DATE_SUB(NOW(), INTERVAL 48 HOUR); TRUNCATE TABLE trs_quest; TRUNCATE TABLE trs_visited; UPDATE settings_device SET walker_id = 6 WHERE walker_id = 2; UPDATE settings_device SET walker_id = 8 WHERE walker_id = 7;"')
    os.system('docker start pokemon_mad')

def check_quests_completed():
    fileTotal = get_file_total_quests()
    if fileTotal["totalQuestsLeiria"] == 248 and fileTotal["totalQuestsMarinha"] == 107:
        return {"leiria": False, "marinha": False}
    scannerTotal = get_scanner_total_quests()

    return {
        "leiria": (scannerTotal["totalQuestsLeiria"] == 248 and fileTotal["totalQuestsLeiria"] < scannerTotal["totalQuestLeiria"]),
        "marinha": (scannerTotal["totalQuestsMarinha"] == 107 and fileTotal["totalQuestsMarinha"] < scannerTotal["totalQuestMarinha"])
    }

def start_pokemon_scan(new_walker_id, old_walker_id):
    os.system('docker stop pokemon_mad')
    os.system(f'docker exec -i pokemon_rocketdb mysql -uroot -pStrongPassword  <<< "use rocketdb; UPDATE settings_device SET walker_id = {new_walker_id} WHERE walker_id = {old_walker_id};"')
    os.system('docker start pokemon_mad')

def get_scanner_total_quests():
    data = requests.get(globals.BACKEND_ENDPOINT + 'get_quests?fence=None')
    questText = data.text
    quests = json.loads(questText)
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