from jinja2 import Environment, FileSystemLoader
import imgkit, os, requests

class ImageGenerator:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.google_api_key = os.environ.get("GOOGLE_API_KEY")

    def generate_image_from_quest_data(self, quest_data, is_leiria):
        env = Environment(loader=FileSystemLoader('templates'))
        template = env.get_template('quests.html')
        html_content = template.render(quests=quest_data, is_leiria=is_leiria)
        options = {
            'format': 'jpg',
            'encoding': 'UTF-8',
            'width': '800',
        }
        imgkit.from_string(html_content, 'quests.jpg', options=options)

    def generate_map_image_from_quest_data(self, quest_data):
            coordinates = []
            for quest in quest_data:
                for sub_quest in quest.get('quests', []):
                    if 'lat' in sub_quest and 'lon' in sub_quest:
                        coordinates.append((sub_quest['lat'], sub_quest['lon']))

            if not coordinates:
                raise ValueError("No valid coordinates found in quest data.")

            base_url = "https://maps.googleapis.com/maps/api/staticmap?"
            params = f"key={self.google_api_key}&size=1280x720"
            markers = "&".join([f"markers=color:red%7Clabel:{idx+1}%7C{lat},{lon}" for idx, (lat, lon) in enumerate(coordinates)])
            url = f"{base_url}{params}&{markers}"

            response = requests.get(url)
            if response.status_code == 200:
                with open('quests_map.jpg', 'wb') as file:
                    file.write(response.content)
            else:
                raise Exception(f"Error fetching the map image: {response.status_code} - {response.text}")
