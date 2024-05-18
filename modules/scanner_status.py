import datetime, requests, discord, pytz, os, aiohttp

class ScannerStatus:
    INITIAL_DOWN_DEVICES_LEIRIA = 7
    INITIAL_DOWN_DEVICES_MARINHA = 1
    TOTAL_DEVICES = 8

    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.BACKEND_ENDPOINT = os.environ.get("BACKEND_ENDPOINT")
        self.LEIRIA_QUEST_SCANNING_URL = os.environ.get("LEIRIA_QUEST_SCANNING_URL")
        self.MARINHA_QUEST_SCANNING_URL = os.environ.get("MARINHA_QUEST_SCANNING_URL")
        
    async def get_workers_with_issues(self):
        worker_status = await self.get_worker_status()
        down_devices_leiria = self.INITIAL_DOWN_DEVICES_LEIRIA
        down_devices_marinha = self.INITIAL_DOWN_DEVICES_MARINHA

        if worker_status and 'workers' in worker_status:
            workers = worker_status['workers']
            for worker in workers:
                if worker.get('isAllocated', False):
                    worker_name = worker.get('controller', {}).get('workerName', '')
                    if "MarinhaGrande" in worker_name:
                        down_devices_marinha -= 1
                    elif "LevelUp" in worker_name:
                        continue
                    else:
                        down_devices_leiria -= 1

        # Ensure the counts do not go negative
        down_devices_leiria = max(down_devices_leiria, 0)
        down_devices_marinha = max(down_devices_marinha, 0)

        return {'down_devices_leiria': down_devices_leiria, 'down_devices_marinha': down_devices_marinha}
    
    async def get_worker_status(self):
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(self.BACKEND_ENDPOINT + 'api/status', timeout=20) as response:
                    response.raise_for_status()  # Raise an exception for HTTP error status codes (4xx or 5xx)
                    return await response.json()
            except (aiohttp.ClientError, aiohttp.TimeoutError) as e:
                print(f"Error: {e}")
                return None

    async def rename_voice_channels(self, leiria_down_counter, marinha_down_counter):
        leiria_status = self.get_status_message(leiria_down_counter, "LEIRIA")
        marinha_status = self.get_status_message(marinha_down_counter, "MARINHA")

        if self.poliswag.VOICE_CHANNEL_LEIRIA.name != leiria_status:
            await self.poliswag.VOICE_CHANNEL_LEIRIA.edit(name=leiria_status)

        if self.poliswag.VOICE_CHANNEL_MARINHA.name != marinha_status:
            await self.poliswag.VOICE_CHANNEL_MARINHA.edit(name=marinha_status)

    def get_status_message(self, downCounter, region):
        if region == "LEIRIA":
            if downCounter > 5:
                return f"{region}: 🔴"
            elif downCounter > 2:
                return f"{region}: 🟠"
            elif downCounter > 0:
                return f"{region}: 🟡"
            else:
                return f"{region}: 🟢"
        elif region == "MARINHA":
            if downCounter > 0:
                return f"{region}: 🔴"
            else:
                return f"{region}: 🟢"
        return f"{region}: ⚪"

    async def is_quest_scanning_complete(self):
        quest_scanning_ongoing = self.poliswag.db.get_data_from_database(f"SELECT scanned FROM poliswag;")
        is_quest_scanning = True if quest_scanning_ongoing['scanned'] == 0 else False
        if not is_quest_scanning:
            return {'leiria_completed': False, 'marinha_completed': False}

        try:
            response_leiria = requests.get(self.LEIRIA_QUEST_SCANNING_URL)
            response_marinha = requests.get(self.MARINHA_QUEST_SCANNING_URL)
            
            response_leiria.raise_for_status()  # Raise an exception for 4xx and 5xx status codes
            response_marinha.raise_for_status()
            
            leiria_data = response_leiria.json()
            marinha_data = response_marinha.json()
            
            leiria_completed = leiria_data.get('total') == leiria_data.get('no_ar_quests') and leiria_data.get('total') == leiria_data.get('ar_quests')
            marinha_completed = marinha_data.get('total') == marinha_data.get('no_ar_quests') and marinha_data.get('total') == marinha_data.get('ar_quests')

            if leiria_completed and marinha_completed:
                self.poliswag.scanner_manager.update_quest_scanning_state()
            
            return {'leiria_completed': leiria_completed, 'marinha_completed': marinha_completed}
        except requests.RequestException as e:
            return {'leiria_completed': False, 'marinha_completed': False}
