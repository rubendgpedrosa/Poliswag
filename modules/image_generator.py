import asyncio
import imgkit
from jinja2 import Environment, FileSystemLoader

from modules.config import Config


class ImageGenerator:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.google_api_key = Config.GOOGLE_API_KEY
        self.TEMPLATE_HTML_DIR = Config.TEMPLATE_HTML_DIR
        self.FOLLOWED_EVENTS_TEMPLATE_HTML_FILE = (
            Config.FOLLOWED_EVENTS_TEMPLATE_HTML_FILE
        )
        self.ACCOUNTS_TEMPLATE_HTML_FILE = Config.ACCOUNTS_TEMPLATE_HTML_FILE
        self.QUEST_ICON_BASE_URL = Config.UI_ICONS_URL

    async def generate_image_from_quest_data(
        self, quests_leiria, quests_marinha, has_leiria, has_marinha
    ):
        env = Environment(loader=FileSystemLoader(self.TEMPLATE_HTML_DIR))
        template = env.get_template(self.FOLLOWED_EVENTS_TEMPLATE_HTML_FILE)
        html_content = template.render(
            quests_leiria=quests_leiria,
            quests_marinha=quests_marinha,
            has_leiria=has_leiria,
            has_marinha=has_marinha,
        )
        options = {
            "format": "png",
            "encoding": "UTF-8",
            "width": "550",
            "height": "600",
            "quality": "100",
            "transparent": "",
            "javascript-delay": "1000",
            "quiet": "",
        }
        try:
            return await asyncio.to_thread(
                imgkit.from_string, html_content, False, options
            )
        except Exception as e:
            self.poliswag.utility.log_to_file(
                f"Error generating quest image: {e}", "ERROR"
            )
            return None

    async def generate_image_from_account_stats(self, account_data, device_status):
        env = Environment(loader=FileSystemLoader(self.TEMPLATE_HTML_DIR))
        template = env.get_template(self.ACCOUNTS_TEMPLATE_HTML_FILE)
        html_content = template.render(
            good=account_data.get("good", 0),
            cooldown=account_data.get("cooldown", 0),
            disabled=account_data.get("disabled", 0),
            device_status=device_status,
        )
        options = {
            "format": "png",
            "encoding": "UTF-8",
            "width": "800",
            "height": "220",
            "quality": "100",
            "transparent": "",
            "javascript-delay": "1000",
            "quiet": "",
        }
        try:
            return await asyncio.to_thread(
                imgkit.from_string, html_content, False, options
            )
        except Exception as e:
            self.poliswag.utility.log_to_file(
                f"Error generating account image: {e}", "ERROR"
            )
            return None

    def generate_static_map_for_group_of_quests(self, pokestops):
        coordinates = []
        for idx, stop in enumerate(pokestops):
            if "lat" in stop and "lon" in stop:
                coordinates.append(
                    (stop["lat"], stop["lon"], chr(65 + idx), stop["quest_slug"])
                )

        if not coordinates:
            return None

        base_url = "https://maps.googleapis.com/maps/api/staticmap?"
        params = f"key={self.google_api_key}&size=600x300&scale=2"
        markers = "&".join(
            [
                f"markers=icon:{self.QUEST_ICON_BASE_URL}{quest_slug}|label:{label}|{lat},{lon}"
                for lat, lon, label, quest_slug in coordinates
            ]
        )
        return f"{base_url}{params}&{markers}"
