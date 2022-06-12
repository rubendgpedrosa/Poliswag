import json, os, requests

import helpers.globals as globals

def fetch_today_data():
    data = requests.get(globals.BACKEND_ENDPOINT + 'get_quests?fence=None')
    questText = data.text
    quests = json.loads(questText)

    os.remove(globals.QUESTS_FILE)

    with open(globals.QUESTS_FILE, 'w') as file:
        json.dump(quests, file, indent=4)