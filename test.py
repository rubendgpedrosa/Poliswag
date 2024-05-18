import requests

# Define the URL for the GraphQL endpoint
url = "http://127.0.0.1:1080/graphql"

# Define the headers for the request
headers = {
    'Content-Type': 'application/json',
    'Cookie': '_ga="GA1.1.1721751726.1681935540"; _ga_ZPJR41B7T2="GS1.1.1714980075.115.0.1714980075.0.0.0"; reactmap1="s:9eET543Mvp1luWFFzISxwHD98KD4Y6Rw.Tdnyr4HT9s1Ct01Ja3DoNc6sucln8rGjMkBj08b1EjY"'
}

# Define the JSON payload for the request
payload = {
    "operationName": "SearchQuests",
    "variables": {
        "search": "dust",
        "category": "quests",
        "lat": 39.742104202459274,
        "lon": -8.804876804351808,
        "locale": "en",
        "onlyAreas": [],
        "questLayer": "both"
    },
    "query": """
        query SearchQuests($search: String!, $category: String!, $lat: Float!, $lon: Float!, $locale: String!, $onlyAreas: [String], $questLayer: String) {
            searchQuest(
                search: $search
                category: $category
                lat: $lat
                lon: $lon
                locale: $locale
                onlyAreas: $onlyAreas
                questLayer: $questLayer
            ) {
                id
                name
                lat
                lon
                distance
                quest_pokemon_id
                quest_form_id
                quest_gender_id
                quest_costume_id
                quest_shiny
                quest_item_id
                quest_reward_type
                mega_pokemon_id
                mega_amount
                stardust_amount
                item_amount
                candy_pokemon_id
                candy_amount
                xp_amount
                with_ar
                quest_title
                quest_target
                __typename
            }
        }
    """
}

# Make the POST request
response = requests.post(url, headers=headers, json=payload)

# Print the response from the server
print(response.json())
