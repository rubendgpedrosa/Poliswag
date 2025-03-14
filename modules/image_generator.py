import os
import imgkit
from PIL import Image
from jinja2 import Environment, FileSystemLoader


class ImageGenerator:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.google_api_key = os.environ.get("GOOGLE_API_KEY")

        self.TEMPLATE_HTML_DIR = os.environ.get("TEMPLATE_HTML_DIR")
        self.FOLLOWED_EVENTS_TEMPLATE_HTML_FILE = os.environ.get(
            "FOLLOWED_EVENTS_TEMPLATE_HTML_FILE"
        )
        self.ACCOUNTS_TEMPLATE_HTML_FILE = os.environ.get("ACCOUNTS_TEMPLATE_HTML_FILE")

        self.QUEST_ICON_BASE_URL = os.environ.get("UI_ICONS_URL")

    def generate_image_from_quest_data(
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
            img = imgkit.from_string(html_content, output_path=False, options=options)
            return img
        except Exception as e:
            print(f"Error generating account image: {e}")
            return None

    def generate_image_from_account_stats(self, account_data):
        env = Environment(loader=FileSystemLoader(self.TEMPLATE_HTML_DIR))
        template = env.get_template(self.ACCOUNTS_TEMPLATE_HTML_FILE)

        good_accounts = account_data.get("good", 0)
        cooldown_accounts = account_data.get("cooldown", 0)
        disabled_accounts = account_data.get("disabled", 0)

        html_content = template.render(
            good=good_accounts, cooldown=cooldown_accounts, disabled=disabled_accounts
        )

        options = {
            "format": "png",
            "encoding": "UTF-8",
            "width": "800",
            "height": "200",
            "quality": "100",
            "transparent": "",
            "javascript-delay": "1000",
            "quiet": "",
        }

        try:
            return imgkit.from_string(html_content, output_path=False, options=options)
        except Exception as e:
            print(f"Error generating account image: {e}")
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
        params = (
            f"key={self.poliswag.image_generator.google_api_key}&size=600x300&scale=2"
        )

        markers = "&".join(
            [
                f"markers=icon:{self.QUEST_ICON_BASE_URL}{quest_slug}|label:{label}|{lat},{lon}"
                for lat, lon, label, quest_slug in coordinates
            ]
        )

        return f"{base_url}{params}&{markers}"
