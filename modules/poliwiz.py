from google import generativeai as genai
from google.api_core import exceptions as google_exceptions
import json
import os
from datetime import datetime, timedelta


class PoliWiz:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        genai.configure(api_key=os.environ["LLM_API_KEY"])
        self.model = genai.GenerativeModel(os.environ["LLM_MODEL"])
        self.cache_file = "data/poliwiz_cache.json"
        self.cache = {"events": {}, "quota": {"reached": False, "timestamp": None}}
        self._load_cache()

    def _load_cache(self):
        if os.path.exists(self.cache_file):
            with open(self.cache_file, "r") as f:
                try:
                    self.cache = json.load(f)
                    # Ensure cache structure is valid
                    if "events" not in self.cache:
                        self.cache["events"] = {}
                    if "quota" not in self.cache:
                        self.cache["quota"] = {
                            "reached": False,
                            "timestamp": None,
                        }
                except json.JSONDecodeError:
                    self.poliswag.utility.log_to_file(
                        f"Error decoding {self.cache_file}. Starting with a fresh cache.",
                        "ERROR",
                    )

    def _save_cache(self):
        with open(self.cache_file, "w") as f:
            json.dump(self.cache, f, indent=4)

    async def process_event(self, event_data):
        # Check quota status
        quota_info = self.cache["quota"]
        if quota_info["reached"]:
            if quota_info["timestamp"]:
                cooldown_end = datetime.fromisoformat(
                    quota_info["timestamp"]
                ) + timedelta(hours=24)
                if datetime.now() < cooldown_end:
                    self.poliswag.utility.log_to_file(
                        f"LLM quota reached. On cooldown until {cooldown_end.isoformat()}",
                        "INFO",
                    )
                    return None
            # Cooldown period has passed, reset quota status
            self.poliswag.utility.log_to_file("LLM cooldown finished.", "INFO")
            quota_info["reached"] = False
            quota_info["timestamp"] = None
            self._save_cache()

        cache_key = json.dumps(event_data, sort_keys=True)
        if cache_key in self.cache["events"]:
            return self.cache["events"][cache_key]

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
            "time_period": "DD/MM/YYYY HH:MM até DD/MM/YYYY HH:MM",
            "type": "Event type (Community Day, Spotlight Hour, etc.)",
            "bonuses": "List all bonuses (XP, Stardust, etc.) or 'None'",
            "featured_pokemon": "Any special spawns or raid bosses or 'None'",
            "special_features": "Field Research, special moves, etc. or 'None'",
            "should_filter": boolean (true if event should be filtered out as unimportant)
        }}
        """

        response_text = ""
        try:
            response = self.model.generate_content(prompt)
            response_text = response.text
            response_text = (
                response_text.replace("```json", "").replace("```", "").strip()
            )
            parsed_data = json.loads(response_text)

            self.cache["events"][cache_key] = parsed_data
            self._save_cache()

            return parsed_data
        except google_exceptions.ResourceExhausted as e:
            self.poliswag.utility.log_to_file(
                f"LLM quota reached: {e}. Starting 24-hour cooldown.", "ERROR"
            )
            self.cache["quota"]["reached"] = True
            self.cache["quota"]["timestamp"] = datetime.now().isoformat()
            self._save_cache()
            return None
        except json.JSONDecodeError as e:
            self.poliswag.utility.log_to_file(
                f"Error parsing response as JSON: {e}, response text was: {response_text}",
                "ERROR",
            )
            return None
        except Exception as e:
            self.poliswag.utility.log_to_file(f"Error processing event: {e}", "ERROR")
            return None
