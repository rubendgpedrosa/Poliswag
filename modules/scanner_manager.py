import docker

from modules.config import Config


class ScannerManager:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.SCANNER_CONTAINER_NAME = Config.SCANNER_CONTAINER_NAME

    async def start_pokestop_scan(self):
        await self.update_last_scanned_date(self.poliswag.utility.time_now())
        await self.update_quest_scanning_state(0)

    async def update_last_scanned_date(self, lastScannedDate):
        await self.poliswag.db.execute_query_to_database(
            "UPDATE poliswag SET last_scanned_date = %s",
            params=(lastScannedDate,),
        )
        self.poliswag.utility.log_to_file(
            f"New last_scanned_date set to {lastScannedDate}"
        )

    async def update_quest_scanning_state(self, state=1):
        await self.poliswag.db.execute_query_to_database(
            "UPDATE poliswag SET scanned = %s",
            params=(state,),
        )
        self.poliswag.utility.log_to_file(
            f"{'Finished' if state == 1 else 'Started'} quest scanning mode"
        )

    async def is_day_change(self):
        last_scanned_date = await self.poliswag.db.get_data_from_database(
            "SELECT last_scanned_date FROM poliswag WHERE last_scanned_date < %s OR last_scanned_date IS NULL",
            params=(self.poliswag.utility.time_now(),),
        )
        if len(last_scanned_date) > 0:
            self.poliswag.utility.log_to_file("Day change encountered")
            await self.start_pokestop_scan()
            return True
        return False

    def change_scanner_status(self, action):
        if not self.SCANNER_CONTAINER_NAME:
            raise ValueError("SCANNER_CONTAINER_NAME environment variable not set.")

        client = None
        try:
            client = docker.from_env()
            container = client.containers.get(self.SCANNER_CONTAINER_NAME)
            if action == "start":
                container.start()
            elif action == "stop":
                container.stop()
            else:
                raise ValueError(
                    f"Invalid action: {action}. Must be 'start' or 'stop'."
                )
        except docker.errors.NotFound:
            raise Exception(f"Container '{self.SCANNER_CONTAINER_NAME}' not found.")
        except docker.errors.APIError as e:
            raise Exception(f"Docker API error: {e}")
        finally:
            if client:
                client.close()
