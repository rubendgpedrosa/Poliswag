from google import generativeai as genai
import json, os


class PoliWiz:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        genai.configure(api_key=os.environ["LLM_API_KEY"])  # configure the api key.
        self.model = genai.GenerativeModel(os.environ["LLM_MODEL"])

    async def process_event(self, event_data):
        prompt = f"""
        #Analyze this Pokémon GO event and extract the following information as structured JSON:

        #Event Data:
        #{json.dumps(event_data)}

        #Rules:
        #- Use European Portuguese (PT-PT) for descriptions.
        #- Keep Pokémon GO terminology in English (Raid, Lure, Lucky Egg, Stardust, Mega Evolution, etc.)
        #- Be concise but include all important information.
        - Format output as structured JSON.
        - Only include information that's valuable to players
        - Don't make up information not in the data

        Return JSON with these fields:
        {{
            "title": "Original event title",
            "time_period": DD/MM/YYYY HH:MM até DD/MM/YYYY HH:MM",
            "type": "Event type (Community Day, Spotlight Hour, etc.)",
            "bonuses": "List all bonuses (XP, Stardust, etc.) or 'None'",
            "featured_pokemon": "Any special spawns or raid bosses or 'None'",
            "special_features": "Field Research, special moves, etc. or 'None'",
            "should_filter": boolean (true if event should be filtered out as unimportant)
        }}
        """

        try:
            response = self.model.generate_content(
                prompt
            )  # generate content directly from the model
            response_text = response.text
            response_text = (
                response_text.replace("```json", "").replace("```", "").strip()
            )
            parsed_data = json.loads(response_text)
            return parsed_data
        except json.JSONDecodeError as e:
            self.poliswag.utility.log_to_file(
                f"Error parsing response as JSON: {e}, response text was: {response_text}",
                "ERROR",
            )
            return None
        except Exception as e:
            self.poliswag.utility.log_to_file(f"Error processing event: {e}", "ERROR")
            return None
