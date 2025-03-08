import datetime
import requests
import os
import time
import discord
import io


class ScannerStatus:
    def __init__(self, poliswag):
        self.poliswag = poliswag

        self.MOCK_DATA_DIR = "mock_data"

        self.channelCache = {
            "leiria": {"name": None, "last_update": 0},
            "marinha": {"name": None, "last_update": 0},
        }

        self.defaultExpectedWorkers = {
            "LeiriaBigger": 3,
            "MarinhaGrande": 1,
        }

        self.last_all_down_request_time = 0
        self.UPDATE_THRESHOLD = 3600  # 1 hour
        self.ALL_DOWN_REQUEST_COOLDOWN = 900  # 15 minutes

    async def get_voice_channel(self, channelName):
        try:
            channel_env_var = f"VOICE_CHANNEL_{channelName.upper()}_ID"
            channel_id = int(os.environ.get(channel_env_var))
            return await self.poliswag.fetch_channel(channel_id)
        except Exception as e:
            self.poliswag.utility.log_to_file(
                f"Error fetching {channelName} channel: {e}", "ERROR"
            )
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

        # Update channels if needed
        for channel_info in [
            ("leiria", leiriaDownCounter, "LEIRIA"),
            ("marinha", marinhaDownCounter, "MARINHA"),
        ]:
            channel_key, counter, region = channel_info
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
                            if e.code == 429:  # Rate limit error
                                self.poliswag.utility.log_to_file(
                                    f"Rate limited while updating {channel_key} channel: {e}",
                                    "ERROR",
                                )
                            else:
                                self.poliswag.utility.log_to_file(
                                    f"Error updating {channel_key} channel: {e}",
                                    "ERROR",
                                )

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
        workerStatus = await self.poliswag.utility.fetch_data("scanner_status")
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
                            if timeDifference <= 600:  # 10-minute threshold
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

    async def check_device_status(self):
        device_status = await self.poliswag.utility.fetch_data("device_status")

        if not device_status or "devices" not in device_status:
            return False

        current_time_ms = datetime.datetime.now().timestamp() * 1000
        inactive_threshold_ms = 600000  # 10 minutes in milliseconds

        for device in device_status["devices"]:
            last_message_time = device.get("dateLastMessageReceived", 0)
            time_since_last_message = current_time_ms - last_message_time

            if time_since_last_message <= inactive_threshold_ms:
                return "🟢"

        return "🔴"

    async def trigger_all_down_action(self):
        current_time = time.time()
        if (
            current_time - self.last_all_down_request_time
            >= self.ALL_DOWN_REQUEST_COOLDOWN
        ):
            self.last_all_down_request_time = current_time
            try:
                device_status = await self.check_device_status()

                account_data = await self.get_account_stats()
                payload = {
                    "type": "map_status",
                    "value": {
                        "accounts": account_data.get("good"),
                        "device_status": device_status,
                    },
                }

                if self.poliswag.utility.DEV:
                    self.poliswag.utility.log_to_file(
                        f"[DEV] Would send all-down notification with payload: {payload}"
                    )
                else:
                    response = requests.post(
                        os.environ.get("ALL_DOWN_ENDPOINT"),
                        json=payload,
                        timeout=10,
                    )
                    response.raise_for_status()

            except requests.exceptions.RequestException as e:
                self.poliswag.utility.log_to_file(
                    f"Error sending all-down notification to myendpoint: {e}",
                    "ERROR",
                )

    def get_status_message(self, downCounter, region):
        if downCounter is None:
            return f"{region}: ❓"

        if region == "MARINHA":
            return f"{region}: {'🟢' if downCounter == 0 else '🔴'}"
        else:
            status_indicators = {0: "🟢", 1: "🟡", 2: "🟠"}
            return f"{region}: {status_indicators.get(downCounter, '🔴')}"

    async def is_quest_scanning_complete(self):
        current_time = datetime.datetime.now()
        if current_time.hour == 0 and current_time.minute < 2:
            return {"leiriaCompleted": False, "marinhaCompleted": False}

        quest_scanning_ongoing = self.poliswag.db.get_data_from_database(
            f"SELECT scanned FROM poliswag;"
        )

        is_quest_scanning = True if quest_scanning_ongoing["scanned"] == 0 else False
        if not is_quest_scanning:
            return {"leiriaCompleted": False, "marinhaCompleted": False}

        try:
            leiria_data = await self.poliswag.utility.fetch_data(
                "leiria_quest_scanning"
            )
            marinha_data = await self.poliswag.utility.fetch_data(
                "marinha_quest_scanning"
            )

            if not leiria_data or not marinha_data:
                return {"leiriaCompleted": False, "marinhaCompleted": False}

            if leiria_data.get("ar_quests") == 0 or marinha_data.get("ar_quests") == 0:
                return {"leiriaCompleted": False, "marinhaCompleted": False}

            leiria_completed = leiria_data.get("total") == leiria_data.get("ar_quests")
            marinha_completed = marinha_data.get("total") == marinha_data.get(
                "ar_quests"
            )

            if leiria_completed and marinha_completed:
                self.poliswag.scanner_manager.update_quest_scanning_state()

            return {
                "leiriaCompleted": leiria_completed,
                "marinhaCompleted": marinha_completed,
            }

        except Exception as e:
            self.poliswag.utility.log_to_file(
                f"Error in quest scanning check: {e}", "ERROR"
            )
            return {"leiriaCompleted": False, "marinhaCompleted": False}

    async def get_account_stats(self):
        account_stats = await self.poliswag.utility.fetch_data("account_status")

        if not account_stats:
            return {
                "in_use": 0,
                "good": 0,
                "cooldown": 0,
                "disabled": 0,
            }

        disabled_accounts = sum(
            [
                account_stats.get(status, 0)
                for status in [
                    "banned",
                    "invalid",
                    "auth_banned",
                    "suspended",
                    "warned",
                    "disabled",
                    "missing_token",
                    "provider_disabled",
                    "zero_last_released",
                ]
            ]
        )

        return {
            "in_use": account_stats.get("in_use", 0),
            "good": account_stats.get("good", 0),
            "cooldown": account_stats.get("cooldown", 0),
            "disabled": disabled_accounts,
        }

    async def update_channel_accounts_stats(self):
        try:
            existing_message = None
            async for message in self.poliswag.ACCOUNTS_CHANNEL.history(limit=None):
                if message.author == self.poliswag.user and not existing_message:
                    existing_message = message
                else:
                    await message.delete()

            account_data = await self.get_account_stats()
            image_bytes = (
                self.poliswag.image_generator.generate_image_from_account_stats(
                    account_data
                )
            )

            if not image_bytes:
                self.poliswag.utility.log_to_file(
                    "Error generating account image", "ERROR"
                )
                return

            try:
                with io.BytesIO(image_bytes) as image_file:
                    discord_file = discord.File(
                        image_file, filename="account_status_report.png"
                    )

                now = datetime.datetime.now()
                timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")

                if existing_message:
                    await existing_message.edit(
                        content=f"*updated at:* {timestamp_str}",
                        attachments=[discord_file],
                    )
                else:
                    await self.poliswag.ACCOUNTS_CHANNEL.send(
                        content=f"*updated at:* {timestamp_str}", file=discord_file
                    )

            except Exception as e:
                self.poliswag.utility.log_to_file(
                    f"Error handling image file: {e}", "ERROR"
                )

        except Exception as e:
            self.poliswag.utility.log_to_file(
                f"An error occurred in update_channel_accounts_stats: {e}", "ERROR"
            )
