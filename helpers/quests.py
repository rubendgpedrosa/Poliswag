import json, os, requests

def fetch_today_data(BACKEND_ENDPOINT, QUESTS_FILE):
    data = requests.get(BACKEND_ENDPOINT + 'get_quests?fence=None')
    questText = data.text
    quests = json.loads(questText)

    os.remove(QUESTS_FILE)

    with open(QUESTS_FILE, 'w') as file:
        json.dump(quests, file, indent=4)