import datetime
import requests
import os
import aiohttp
import time
import discord
import io


class ScannerStatus:
    def __init__(self, poliswag):
        self.poliswag = poliswag

        self.BACKEND_ENDPOINT = os.environ.get("BACKEND_ENDPOINT")
        self.SCANNER_STATUS_URL = os.environ.get("SCANNER_STATUS_URL")
        self.SCANNER_ACCOUNTS_STATUS_URL = os.environ.get("SCANNER_ACCOUNTS_STATUS_URL")
        self.LEIRIA_QUEST_SCANNING_URL = os.environ.get("LEIRIA_QUEST_SCANNING_URL")
        self.MARINHA_QUEST_SCANNING_URL = os.environ.get("MARINHA_QUEST_SCANNING_URL")
        self.UPDATE_THRESHOLD = 3600

        self.channelCache = {
            "leiria": {"name": None, "last_update": 0},
            "marinha": {"name": None, "last_update": 0},
        }
        self.defaultExpectedWorkers = {
            "LeiriaBigger": 3,
            "MarinhaGrande": 1,
        }

    async def get_voice_channel(self, channelName):
        try:
            if channelName == "leiria":
                return await self.poliswag.fetch_channel(
                    int(os.environ.get("VOICE_CHANNEL_LEIRIA_ID"))
                )
            elif channelName == "marinha":
                return await self.poliswag.fetch_channel(
                    int(os.environ.get("VOICE_CHANNEL_MARINHA_ID"))
                )
        except Exception as e:
            print(f"Error fetching {channelName} channel: {e}")
            return None

    async def rename_voice_channels(self, leiriaDownCounter, marinhaDownCounter):
        current_time = time.time()
        updated_channel = False

        if self.should_update_channel("leiria", leiriaDownCounter):
            leiria_status = self.get_status_message(leiriaDownCounter, "LEIRIA")
            if leiria_status != self.channelCache["leiria"]["name"]:
                channel = await self.get_voice_channel("leiria")
                if channel:
                    try:
                        await channel.edit(name=leiria_status)
                        self.channelCache["leiria"] = {
                            "name": leiria_status,
                            "last_update": current_time,
                        }
                        updated_channel = True
                    except discord.errors.HTTPException as e:
                        if e.code == 429:  # Rate limit error
                            print(f"Rate limited while updating Leiria channel: {e}")
                        else:
                            print(f"Error updating Leiria channel: {e}")

        if self.should_update_channel("marinha", marinhaDownCounter):
            marinha_status = self.get_status_message(marinhaDownCounter, "MARINHA")
            if marinha_status != self.channelCache["marinha"]["name"]:
                channel = await self.get_voice_channel("marinha")
                if channel:
                    try:
                        await channel.edit(name=marinha_status)
                        self.channelCache["marinha"] = {
                            "name": marinha_status,
                            "last_update": current_time,
                        }
                        updated_channel = True
                    except discord.errors.HTTPException as e:
                        if e.code == 429:  # Rate limit error
                            print(f"Rate limited while updating Marinha channel: {e}")
                        else:
                            print(f"Error updating Marinha channel: {e}")

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
        workerStatus = await self.get_worker_status()
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

    async def get_worker_status(self):
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(self.SCANNER_STATUS_URL, timeout=20) as response:
                    response.raise_for_status()
                    return await response.json()
            except Exception as e:
                print(f"Error encountered: {e}")
                return None

    def get_status_message(self, downCounter, region):
        if downCounter is None:
            return f"{region}: ❓"

        if region == "MARINHA":
            if downCounter == 0:
                return f"{region}: 🟢"
            else:
                return f"{region}: 🔴"
        else:
            if downCounter == 0:
                return f"{region}: 🟢"
            elif downCounter == 1:
                return f"{region}: 🟡"
            elif downCounter == 2:
                return f"{region}: 🟠"
            elif downCounter >= 3:
                return f"{region}: 🔴"
            else:
                return f"{region}: 🔴"

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
            response_leiria = requests.get(self.LEIRIA_QUEST_SCANNING_URL)
            response_marinha = requests.get(self.MARINHA_QUEST_SCANNING_URL)

            response_leiria.raise_for_status()
            response_marinha.raise_for_status()

            leiria_data = response_leiria.json()
            marinha_data = response_marinha.json()

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
        except requests.RequestException as e:
            return {"leiriaCompleted": False, "marinhaCompleted": False}

    async def get_account_stats(self):
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    self.SCANNER_ACCOUNTS_STATUS_URL, timeout=20
                ) as response:
                    response.raise_for_status()
                    account_stats = await response.json()

                    in_use_accounts = account_stats.get("in_use", 0)
                    good_accounts = account_stats.get("good", 0)
                    cooldown_accounts = account_stats.get("cooldown", 0)
                    disabled_accounts = (
                        account_stats.get("banned", 0)
                        + account_stats.get("invalid", 0)
                        + account_stats.get("auth_banned", 0)
                        + account_stats.get("suspended", 0)
                        + account_stats.get("warned", 0)
                        + account_stats.get("disabled", 0)
                        + account_stats.get("missing_token", 0)
                        + account_stats.get("provider_disabled", 0)
                        + account_stats.get("zero_last_released", 0)
                    )

                    return {
                        "in_use": in_use_accounts,
                        "good": good_accounts,
                        "cooldown": cooldown_accounts,
                        "disabled": disabled_accounts,
                    }
            except Exception as e:
                print(f"Error encountered getting stats: {e}")
                return {
                    "in_use": 0,
                    "good": 0,
                    "cooldown": 0,
                    "disabled": 0,
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
                error_message = "Error generating account image"
                self.poliswag.utility.log_to_file(error_message, "ERROR")
                return

            try:
                with io.BytesIO(image_bytes) as image_file:
                    discord_file = discord.File(
                        image_file, filename="account_status_report.png"
                    )

                if existing_message:
                    now = datetime.datetime.now()
                    timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
                    await existing_message.edit(
                        content=f"*updated at:* {timestamp_str}",
                        attachments=[discord_file],
                    )
                else:
                    await self.poliswag.ACCOUNTS_CHANNEL.send(file=discord_file)

            except Exception as e:
                error_message = f"Error handling image file: {e}"
                self.poliswag.utility.log_to_file(error_message, "ERROR")
                return

        except Exception as e:
            error_message = f"An error occurred in update_channel_accounts_stats: {e}"
            self.poliswag.utility.log_to_file(error_message, "ERROR")
