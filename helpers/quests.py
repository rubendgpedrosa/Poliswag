import json, requests, datetime, discord
import helpers.constants as constants
from helpers.utilities import build_embed_object_title_description
from helpers.database_connector import get_data_from_database, execute_query_to_database

namesList = ["pokemon", "pokemonuteis"]
discordMessageChannels = {"pokemon": "Spawns Raros", "pokemonuteis": "Spawns Uteis"}
currentDay = datetime.datetime.now().day

def get_current_quest_data():
    try:
        data = requests.get(constants.BACKEND_ENDPOINT + 'get_quests?fence=None', timeout=5)
        data.raise_for_status() # Raise an exception if there is an HTTP error status code (4xx or 5xx)
        questText = data.text
        quests = json.loads(questText)
        with open(constants.QUESTS_FILE, 'w') as file:
            json.dump(quests, file, indent=4)
        # Done for the mobile app
        categorize_quests(quests)
        return quests
    except (requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
        print(f"Error: {e}")
        return None

def write_filter_data(receivedData, add=True):
    if len(receivedData) != 2:
        return False

    originalDiscordChannelName = {"raros": ["pokemon", "pokemonmarinha"], "uteis": ["pokemonuteis", "pokemonuteismarinha"]}
    filterName = {"raros": "spawns-raros", "uteis": "spawns-uteis", "pvp": "spawns-pvp"}

    with open(constants.FILTER_FILE) as raw_data:
        jsonPokemonData = json.load(raw_data)

    try:
        pokemon = receivedData[0].title()
        affectedChannel = originalDiscordChannelName[receivedData[1]]
    except:
        return "Não reconheço esse comando. Aqui tens uma lista para te ajudar." + "\n\n__**COMANDOS IMPLEMENTADOS:**__\n\n> !add POKEMON CANAL\nPara se adicionar um Pokémon a um canal específico `(ex:!add Poliwag uteis)`\n> !remove POKEMON CANAL\nPara que se remova um Pokemon de um canal específico `(ex:!remove Poliwag raros)`\n> !reload\nApós se alterar a lista, podem reiniciar as notificações com as alterações usando `(ex:!reload)`"

    for name in affectedChannel:
        if add and pokemon not in jsonPokemonData['monsters']['filters'][name]['monsters']:
            jsonPokemonData['monsters']['filters'][name]['monsters'].append(pokemon)
        elif not add and pokemon in jsonPokemonData['monsters']['filters'][name]['monsters']:
            jsonPokemonData['monsters']['filters'][name]['monsters'].remove(pokemon)

    with open(constants.FILTER_FILE, 'w') as file:
        json.dump(jsonPokemonData, file, indent=4)

    return pokemon + (" adicionado a " if add else " removido de ") + filterName[receivedData[1]]

def find_quest(receivedData, leiria):
    if receivedData and (receivedData == "!questleiria" or receivedData == "!questmarinha"):
        return False

    quests = retrieve_sort_quest_data()

    allQuestData = []
    allQuestDataMarinha = []

    for quest in quests:
        reward = build_reward_for_quest(quest)
        if receivedData and receivedData.lower() in reward.lower() or receivedData.lower() in quest['quest_task'].lower() or receivedData.lower() in quest["name"].lower():
            if "-8.9" not in str(quest['longitude']):
                allQuestData.append({
                    "name": "[Leiria] " + quest["name"],
                    "map": build_quest_location_url(quest["latitude"], quest["longitude"]),
                    "quest": quest['quest_task'] + " - " + reward,
                    "image": quest["url"]
                })
            else:
                allQuestDataMarinha.append({
                    "name": "[Marinha Grande] " + quest["name"],
                    "map": build_quest_location_url(quest["latitude"], quest["longitude"]),
                    "quest": quest['quest_task'] + " - " + reward,
                    "image": quest["url"]
                })

    if leiria:
        return allQuestData
    return allQuestDataMarinha

def retrieve_sort_quest_data():
    with open(constants.QUESTS_FILE) as raw_data:
        quests = json.load(raw_data)
    sortedQuests = sorted(quests, key=lambda k: k['quest_task'], reverse=True)
    return list(filter(lambda q: q["name"] not in [d["name"] for d in sortedQuests[:sortedQuests.index(q)]], sortedQuests))

def build_reward_for_quest(quest): 
    if quest['quest_reward_type'] == 'Pokemon':
        reward = quest['pokemon_name']
    elif quest['quest_reward_type'] == 'Item':
        reward = str(quest['item_amount']) + " " + quest['item_type']
    elif quest['quest_reward_type'] == 'Stardust':
        reward = str(quest['item_amount']) + " " + quest['quest_reward_type']
    else:
        reward = str(quest['item_amount']) + " " + quest['pokemon_name'] + " " + quest['item_type']
    return reward

def build_quest_message(data):
    return "[" + data['name'] + "](" + build_quest_location_url(data["latitude"], data["longitude"]) + ")"

def build_quest_location_url(latitude, longitude):
    coordinatesUrl = "https://www.google.com/maps/search/?api=1&query=" + str(latitude) + "," + str(longitude)

    return coordinatesUrl

def categorize_quests(quests, path='/root/PoGoLeiria/'):
    classifications = {
        'catching': [],
        'throwing': [],
        'battling': [],
        'buddy': [],
        'others': []
    }
    for quest in quests:
        quest_task = quest['quest_task'].lower()
        if 'catch' in quest_task:
            classifications['catching'].append(quest)
        elif 'make' in quest_task or 'throw' in quest_task:
            classifications['throwing'].append(quest)
        elif 'raid' in quest_task or 'battle' in quest_task or 'shadow' in quest_task:
            classifications['battling'].append(quest)
        elif 'gift' in quest_task or 'buddy' in quest_task:
            classifications['buddy'].append(quest)
        else:
            classifications['others'].append(quest)
    
    for key, quests in classifications.items():
        classified_quests = []
        for quest in quests:
            quest_task = quest['quest_task']
            quest_reward = build_reward_for_quest(quest)
            quest_reward_type = quest['quest_reward_type']
            quest_reward_amount = quest['item_amount']
            quest_reward_item_name = quest['item_type']
            quest_reward_item_id = quest['item_id']
            quest_reward_pokemon_name = quest['pokemon_name']
            quest_reward_pokemon_id = quest['pokemon_id']
            found = False
            
            quest_stripped_down = {
                "pokestop_id": quest["pokestop_id"],
                "name": quest["name"],
                "latitude": quest["latitude"],
                "longitude": quest["longitude"],
            }
            
            for cq in classified_quests:
                if cq["quest_description"] == quest_task and cq["quest_reward"] == quest_reward:
                    cq["pokestops"].append(quest_stripped_down)
                    found = True
                    break
            if not found:
                classified_quest = {
                    "quest_description": quest_task,
                    "quest_reward": quest_reward,
                    "quest_reward_type": quest_reward_type,
                    "quest_reward_amount": quest_reward_amount,
                    "quest_reward_item_name": quest_reward_item_name,
                    "quest_reward_pokemon_name": quest_reward_pokemon_name,
                    "quest_reward_pokemon_id": quest_reward_pokemon_id,
                    "quest_reward_item_id": quest_reward_item_id,
                    "pokestops": [quest_stripped_down]
                }
                classified_quests.append(classified_quest)
        with open(f'{path}{key}.json', 'w') as f:
            f.write(json.dumps(classified_quests, indent=4))

def build_quest_summary_embed_objects(quests, storedTrackedRewards):
    questGroupsLeiria = {}
    questGroupsMarinhaGrande = {}

    for quest in quests:
        longitude = str(quest['longitude'])
        if "-8.9" not in longitude:
            questGroupsLeiria = update_quest_groups(quest, questGroupsLeiria, storedTrackedRewards)
        else:
            questGroupsMarinhaGrande = update_quest_groups(quest, questGroupsMarinhaGrande, storedTrackedRewards)

    # embedLeiria = discord.Embed(title="Resumo de algumas quests de hoje - Leiria", color=0x7b83b4)
    # embedMarinhaGrande = discord.Embed(title="Resumo de algumas quests de hoje - Marinha Grande", color=0x7b83b4)
    # lessPopularQuestsLeiria = get_least_popular_quests(questGroupsLeiria)
    # lessPopularQuestsMarinhaGrande = get_least_popular_quests(questGroupsMarinhaGrande)
    # embedLeiria.add_field(name="Quests Leiria", value=questSummaryListStringLeiria)
    # embedMarinhaGrande.add_field(name="Quests Marinha Grande", value=questSummaryListStringMarinhaGrande)
    # embedLeiria.set_footer(text="Esta informação expira ao final do dia")
    # embedMarinhaGrande.set_footer(text="Esta informação expira ao final do dia")

    questSummaryListLeiria = build_quest_summary_list_embed(questGroupsLeiria, "SUMÁRIO DE QUESTS - LEIRIA")
    questSummaryListMarinhaGrande = build_quest_summary_list_embed(questGroupsMarinhaGrande, "SUMÁRIO DE QUESTS - MARINHA GRANDE")

    return {'Leiria': questSummaryListLeiria, 'MarinhaGrande': questSummaryListMarinhaGrande}

def update_quest_groups(quest, questGroups, storedTrackedRewards):
    questTask = quest['quest_task']
    reward = build_reward_for_quest(quest)
    
    rewardLower = reward.lower()
    storedTrackedRewardsLower = [keyword.lower() for keyword in storedTrackedRewards]
    if any(keyword in rewardLower for keyword in storedTrackedRewardsLower):
        if reward in questGroups:
            if questTask in questGroups[reward]["rewards"]:
                questGroups[reward]["count"] += 1
            else:
                questGroups[reward]["count"] += 1
                questGroups[reward]["rewards"].append(questTask)
        else:
            questGroups[reward] = {"count": 1, "rewards": [questTask]}
    
    return questGroups

def build_quest_summary_list_embed(questGroups, title):
    if len(questGroups) == 0:
        return None
    
    embed = discord.Embed(
        title=title,
        description="Lista de atuais quests ativas consideradas relevantes.",
        color=0x7b83b4
    )
    
    sortedQuestGroups = dict(sorted(questGroups.items()))
    
    for questKey, questInfo in sortedQuestGroups.items():
        count = questInfo["count"]
        rewards = "\n⦁ ".join(questInfo["rewards"])
        embed.add_field(name=f"({count}x) {questKey.upper()}", value=f"⦁ {rewards}", inline=False)
    
    return embed

def get_least_popular_quests(questGroups):
    questCount = {k: v["count"] for k, v in questGroups.items()}
    sortedQuests = sorted(questCount.items(), key=lambda x: x[1])

    leastPopularQuests = []
    leastPopularCount = sortedQuests[0][1]
    for quest, count in sortedQuests:
        if count <= leastPopularCount:
            leastPopularQuests.append((quest, count))
        else:
            break

    # Check if there are more quests with the same count as the 10th least popular quest
    for quest, count in sortedQuests[len(leastPopularQuests):]:
        if count == leastPopularCount:
            leastPopularQuests.append((quest, count))
        else:
            break

    return leastPopularQuests

def update_tracking_entries(rewardToTrack, message):
    if message.content.startswith("!untrackall"):
        execute_query_to_database(f"TRUNCATE TABLE track_reward;", "poliswag")
    elif message.content.startswith("!track"):
        execute_query_to_database(f"INSERT INTO track_reward (reward, creator) VALUES ('{rewardToTrack}', '{message.author}');", "poliswag")
    elif message.content.startswith("!untrack"):
        execute_query_to_database(f"DELETE FROM track_reward WHERE reward = '{rewardToTrack}';", "poliswag")


def get_tracked_rewards():
    storedTrackedRewards = get_data_from_database(f"SELECT reward FROM track_reward;", "poliswag")

    storedRewards = []
    if len(storedTrackedRewards) > 0:
        for storedTrackedReward in storedTrackedRewards:
            storedRewards.append(storedTrackedReward["data"][0])
        return build_quest_summary_embed_objects(retrieve_sort_quest_data(), storedRewards)
    
    return {'Leiria': None, 'MarinhaGrande': None} 

async def retrieve_quest_summary(channel, notify=False):
    trackedRewards = get_tracked_rewards()
    
    if trackedRewards['Leiria'] is not None:
        await channel.send(embed=trackedRewards['Leiria'])
        
    if trackedRewards['MarinhaGrande'] is not None:
        await channel.send(embed=trackedRewards['MarinhaGrande'])
        
    if trackedRewards['Leiria'] is None and trackedRewards['MarinhaGrande'] is None and notify:
        await channel.send(embed=build_embed_object_title_description("No rewards are currently being tracked"))

async def list_track_quest(channel):
    storedTrackedRewards = get_data_from_database(f"SELECT reward, createddate FROM track_reward;", "poliswag")

    storedRewards = []
    if len(storedTrackedRewards) > 0:
        for storedTrackedReward in storedTrackedRewards:
            storedRewards.append(f"{storedTrackedReward['data'][0].capitalize()} => created date: {storedTrackedReward['data'][1]}")
        await channel.send(embed=build_embed_object_title_description("Rewards being tracked", "\n".join(storedRewards)))
    else:
        await channel.send(embed=build_embed_object_title_description("No rewards are currently being tracked"))