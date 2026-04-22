import datetime
import time
import discord
from modules.http_client import fetch_data
from modules.config import Config


class ScannerStatus:
    def __init__(self, poliswag):
        self.poliswag = poliswag

        self.channelCache = {
            "leiria": {"name": None, "last_update": 0},
            "marinha": {"name": None, "last_update": 0},
        }

        self.defaultExpectedWorkers = {
            "LeiriaBigger": 4,
            "MarinhaGrande": 1,
        }

        self.last_all_down_request_time = 0
        self.UPDATE_THRESHOLD = 3600  # 1 hour
        self.ALL_DOWN_REQUEST_COOLDOWN = 900  # 15 minutes

    def _log(self, msg, level="ERROR"):
        self.poliswag.utility.log_to_file(msg, level)

    async def get_voice_channel(self, channelName):
        try:
            channel_id = Config.VOICE_CHANNELS.get(channelName.lower())
            if not channel_id:
                self._log(f"No voice channel configured for '{channelName}'")
                return None
            return await self.poliswag.fetch_channel(channel_id)
        except Exception as e:
            self._log(f"Error fetching {channelName} channel: {e}")
            return None

    async def rename_voice_channels(self, leiriaDownCounter, marinhaDownCounter):
        current_time = time.time()

        if (
            leiriaDownCounter is not None
            and marinhaDownCounter is not None
            and leiriaDownCounter >= self.defaultExpectedWorkers["LeiriaBigger"]
            and marinhaDownCounter >= self.defaultExpectedWorkers["MarinhaGrande"]
        ):
            await self.trigger_all_down_action()

        for channel_key, counter, region in [
            ("leiria", leiriaDownCounter, "LEIRIA"),
            ("marinha", marinhaDownCounter, "MARINHA"),
        ]:
            if self.should_update_channel(channel_key, counter):
                status = self.get_status_message(counter, region)
                if status != self.channelCache[channel_key]["name"]:
                    channel = await self.get_voice_channel(channel_key)
                    if channel:
                        try:
                            await channel.edit(name=status)
                            self.channelCache[channel_key] = {
                                "name": status,
                                "last_update": current_time,
                            }
                        except discord.errors.HTTPException as e:
                            if e.code == 429:
                                self._log(
                                    f"Rate limited while updating {channel_key} channel: {e}"
                                )
                            else:
                                self._log(f"Error updating {channel_key} channel: {e}")

    def should_update_channel(self, channelType, counter):
        current_time = time.time()
        cacheEntry = self.channelCache[channelType]

        if (
            cacheEntry["name"] is None
            or current_time - cacheEntry["last_update"] >= self.UPDATE_THRESHOLD
        ):
            return True

        new_status = self.get_status_message(counter, channelType.upper())
        return new_status != cacheEntry["name"]

    async def get_workers_with_issues(self):
        workerStatus = await fetch_data("scanner_status", log_fn=self._log)
        downDevicesLeiria = None
        downDevicesMarinha = None

        if workerStatus and "areas" in workerStatus:
            for area in workerStatus["areas"]:
                areaName = area.get("name")
                expectedWorkers = self.defaultExpectedWorkers.get(areaName)

                if "worker_managers" in area and area["worker_managers"]:
                    expectedWorkersFromResponse = area["worker_managers"][0].get(
                        "expected_workers"
                    )
                    if expectedWorkersFromResponse is not None:
                        expectedWorkers = expectedWorkersFromResponse

                # Skip areas we have no baseline for — without an expected worker
                # count there is nothing to decrement, and the down-count cannot
                # be expressed meaningfully.
                if expectedWorkers is None:
                    continue

                downDevices = expectedWorkers
                for workerManager in area.get("worker_managers", []):
                    for worker in workerManager.get("workers", []):
                        lastData = worker.get("last_data")
                        connectionStatus = worker.get("connection_status")

                        isWorkerUp = False
                        if lastData is not None:
                            timeDifference = (
                                datetime.datetime.now().timestamp() - lastData
                            )
                            if timeDifference <= 600:
                                if connectionStatus == "Executing Worker":
                                    isWorkerUp = True

                        if isWorkerUp:
                            downDevices -= 1

                if areaName == "LeiriaBigger":
                    downDevicesLeiria = (
                        max(downDevices, 0) if downDevices is not None else None
                    )
                elif areaName == "MarinhaGrande":
                    downDevicesMarinha = (
                        max(downDevices, 0) if downDevices is not None else None
                    )

        return {
            "downDevicesLeiria": downDevicesLeiria,
            "downDevicesMarinha": downDevicesMarinha,
        }

    async def trigger_all_down_action(self):
        current_time = time.time()
        if (
            current_time - self.last_all_down_request_time
            >= self.ALL_DOWN_REQUEST_COOLDOWN
        ):
            self.last_all_down_request_time = current_time
            try:
                device_status = (
                    await self.poliswag.account_monitor.is_device_connected()
                )
                account_data = await self.poliswag.account_monitor.get_account_stats()
                payload = {
                    "type": "map_status",
                    "value": {
                        "accounts": account_data.get("good"),
                        "device_status": device_status,
                    },
                }

                if not Config.IS_PRODUCTION:
                    self._log(
                        f"[DEV] Would send all-down notification with payload: {payload}",
                        "INFO",
                    )
                else:
                    await fetch_data(
                        "all_down", log_fn=self._log, method="POST", data=payload
                    )
            except Exception as e:
                self._log(f"Error sending all-down notification: {e}")

    def get_status_message(self, downCounter, region):
        if downCounter is None:
            return f"{region}: ❓"

        if region == "MARINHA":
            return f"{region}: {'🟢' if downCounter == 0 else '🔴'}"
        else:
            expected_workers = self.defaultExpectedWorkers.get("LeiriaBigger", 5)
            down_percentage = (
                (downCounter / expected_workers) if expected_workers > 0 else 0
            )

            if down_percentage == 0:
                status_indicator = "🟢"
            elif down_percentage <= 0.4:
                status_indicator = "🟡"
            elif down_percentage <= 0.8:
                status_indicator = "🟠"
            else:
                status_indicator = "🔴"

            return f"{region}: {status_indicator}"

    async def is_quest_scanning_complete(self):
        current_time = datetime.datetime.now()
        if current_time.hour == 0 and current_time.minute < 2:
            return None

        quest_scanning_ongoing = self.poliswag.db.get_data_from_database(
            "SELECT scanned FROM poliswag WHERE scanned = 1;"
        )

        if quest_scanning_ongoing and len(quest_scanning_ongoing) > 0:
            return None

        try:
            leiria_data = await fetch_data("leiria_quest_scanning", log_fn=self._log)
            marinha_data = await fetch_data("marinha_quest_scanning", log_fn=self._log)

            if not leiria_data or not marinha_data:
                return None

            if leiria_data.get("ar_quests") == 0 or marinha_data.get("ar_quests") == 0:
                return None

            leiria_total = leiria_data.get("total") or 0
            leiria_ar_quests = leiria_data.get("ar_quests") or 0
            marinha_total = marinha_data.get("total") or 0
            marinha_ar_quests = marinha_data.get("ar_quests") or 0

            leiria_percentage = (
                (leiria_ar_quests / leiria_total * 100) if leiria_total > 0 else 0
            )
            marinha_percentage = (
                (marinha_ar_quests / marinha_total * 100) if marinha_total > 0 else 0
            )

            leiria_threshold = leiria_total * 0.98
            marinha_threshold = marinha_total * 0.98
            return {
                "leiriaCompleted": leiria_ar_quests >= leiria_threshold,
                "marinhaCompleted": marinha_ar_quests >= marinha_threshold,
                "leiriaTotal": leiria_total,
                "leiriaScanned": leiria_ar_quests,
                "marinhaTotal": marinha_total,
                "marinhaScanned": marinha_ar_quests,
                "leiriaPercentage": leiria_percentage,
                "marinhaPercentage": marinha_percentage,
            }

        except Exception as e:
            self._log(f"Error in quest scanning check: {e}")
            return None
