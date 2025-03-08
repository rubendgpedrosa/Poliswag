import os
import requests
import imgkit
import io
from PIL import Image
from jinja2 import Environment, FileSystemLoader


class ImageGenerator:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.google_api_key = os.environ.get("GOOGLE_API_KEY")

        self.TEMPLATE_HTML_DIR = os.environ.get("TEMPLATE_HTML_DIR")
        self.QUESTS_TEMPLATE_HTML_FILE = os.environ.get("QUESTS_TEMPLATE_HTML_FILE")
        self.ACCOUNTS_TEMPLATE_HTML_FILE = os.environ.get("ACCOUNTS_TEMPLATE_HTML_FILE")

        self.QUEST_ICON_BASE_URL = os.environ.get("UI_ICONS_URL")

        self.quests_image_bytes = None
        self.quests_map_bytes = None

    def generate_image_from_quest_data(self, quest_data, is_leiria):
        env = Environment(loader=FileSystemLoader(self.TEMPLATE_HTML_DIR))
        template = env.get_template(self.QUESTS_TEMPLATE_HTML_FILE)

        for quest_list in quest_data:
            quest_list["quests"].sort(key=lambda x: x["name"].lower())

        html_content = template.render(quests=quest_data, is_leiria=is_leiria)
        options = {
            "format": "png",
            "encoding": "UTF-8",
            "width": "1200",
            "quality": "80",
            "quiet": "",
        }
        self.quests_image_bytes = imgkit.from_string(
            html_content, output_path=False, options=options
        )

    def generate_map_image_from_quest_data(self, quest_data):
        coordinates = []
        for quest in quest_data:
            for sub_quest in quest.get("quests", []):
                if "lat" in sub_quest and "lon" in sub_quest:
                    coordinates.append((sub_quest["lat"], sub_quest["lon"]))

        if not coordinates:
            raise ValueError("No valid coordinates found in quest data.")

        base_url = "https://maps.googleapis.com/maps/api/staticmap?"
        params = f"key={self.google_api_key}&size=1280x720"
        markers = "&".join(
            [
                f"markers=color:red%7Clabel:{chr(65 + idx)}%7C{lat},{lon}"
                for idx, (lat, lon) in enumerate(coordinates)
            ]
        )
        url = f"{base_url}{params}&{markers}"

        response = requests.get(url)
        if response.status_code == 200:
            self.quests_map_bytes = response.content
        else:
            raise Exception(
                f"Error fetching the map image: {response.status_code} - {response.text}"
            )

    def combine_images(self):
        try:
            if self.quests_image_bytes is None or self.quests_map_bytes is None:
                print("Required image bytes not available.")
                return None

            quest_image = Image.open(io.BytesIO(self.quests_image_bytes)).convert(
                "RGBA"
            )
            map_image = Image.open(io.BytesIO(self.quests_map_bytes)).convert("RGBA")

            print(f"Quest Image Size: {quest_image.size}")
            print(f"Map Image Size: {map_image.size}")
            map_image = map_image.resize(
                (
                    quest_image.width,
                    int(quest_image.width * (map_image.height / map_image.width)),
                )
            )
            print(f"Resized Map Image Size: {map_image.size}")

            combined_image = Image.new(
                "RGBA", (quest_image.width, quest_image.height + map_image.height + 20)
            )
            print("Combined image created.")
            combined_image.paste(quest_image, (0, 0))
            combined_image.paste(map_image, (0, quest_image.height + 20))

            output_buffer = io.BytesIO()
            combined_image.save(output_buffer, "PNG")
            output_buffer.seek(0)
            return output_buffer
        except FileNotFoundError as e:
            print(f"Error opening image bytes: {e}")
            return None
        except Exception as e:
            print(f"An error occurred during image combination: {e}")
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
            img = imgkit.from_string(html_content, output_path=False, options=options)
            return img
        except Exception as e:
            print(f"Error generating account image: {e}")
            return None

    def generate_static_map_for_group(self, pokestops):
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
