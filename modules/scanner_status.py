import datetime, requests, discord, pytz, os, aiohttp


class ScannerStatus:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.BACKEND_ENDPOINT = os.environ.get("BACKEND_ENDPOINT")
        self.SCANNER_STATUS_URL = os.environ.get("SCANNER_STATUS_URL")
        self.SCANNER_ACCOUNTS_STATUS_URL = os.environ.get("SCANNER_ACCOUNTS_STATUS_URL")
        self.LEIRIA_QUEST_SCANNING_URL = os.environ.get("LEIRIA_QUEST_SCANNING_URL")
        self.MARINHA_QUEST_SCANNING_URL = os.environ.get("MARINHA_QUEST_SCANNING_URL")

    async def get_workers_with_issues(self):
        workerStatus = await self.get_worker_status()
        downDevicesLeiria = None
        downDevicesMarinha = None

        if workerStatus and "areas" in workerStatus:
            for area in workerStatus["areas"]:
                expectedWorkers = 0
                if "worker_managers" in area and area["worker_managers"]:
                    expectedWorkers = area["worker_managers"][0].get(
                        "expected_workers", 0
                    )

                downDevices = expectedWorkers
                print(
                    f"Area: {area.get('name')}, Expected Workers: {expectedWorkers}"
                )  # Log expected workers

                for workerManager in area.get("worker_managers", []):
                    for worker in workerManager.get("workers", []):
                        print("--- Worker Start ---")  # Start of worker log
                        print(f"Worker Data: {worker}")  # Log worker data

                        last_data = worker.get("last_data")
                        connection_status = worker.get("connection_status")

                        print(f"Last Data: {last_data}")  # Log last data
                        print(
                            f"Connection Status: {connection_status}"
                        )  # Log connection status

                        isWorkerUp = False

                        if last_data is not None:
                            time_difference = (
                                datetime.datetime.now().timestamp() - last_data
                            )
                            print(
                                f"Time Difference: {time_difference}"
                            )  # Log time difference
                            if time_difference <= 300:  # 5-minute threshold
                                if connection_status == "Executing Worker":
                                    isWorkerUp = True
                                elif connection_status == "No accounts available":
                                    isWorkerUp = False  # Consider "No accounts available" as down
                                else:
                                    print(
                                        f"Unknown connection status: {connection_status}"
                                    )  # Log unknown status
                            else:
                                print("Last data is too old.")  # Log old data
                        else:
                            print("Last data is missing.")  # Log missing data

                        print(f"isWorkerUp: {isWorkerUp}")  # Log isWorkerUp status

                        if isWorkerUp:
                            downDevices -= 1
                        print("--- Worker End ---")  # End of worker log

                if area.get("name") == "LeiriaBigger":
                    downDevicesLeiria = (
                        max(downDevices, 0) if downDevices is not None else None
                    )
                elif area.get("name") == "MarinhaGrande":
                    downDevicesMarinha = (
                        max(downDevices, 0) if downDevices is not None else None
                    )

                print(
                    f"Area: {area.get('name')}, Down Devices: {downDevices}"
                )  # Log down devices for the area

        print(
            {
                "downDevicesLeiria": downDevicesLeiria,
                "downDevicesMarinha": downDevicesMarinha,
            }
        )  # Log final counts
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

    async def rename_voice_channels(self, leiriaDownCounter, marinhaDownCounter):
        if self.poliswag.VOICE_CHANNEL_LEIRIA:  # Check if channel exists
            leiriaStatus = self.get_status_message(leiriaDownCounter, "LEIRIA")
            if self.poliswag.VOICE_CHANNEL_LEIRIA.name != leiriaStatus:
                try:
                    await self.poliswag.VOICE_CHANNEL_LEIRIA.edit(name=leiriaStatus)
                except Exception as e:
                    print(f"Error editing Leiria channel: {e}")

        if self.poliswag.VOICE_CHANNEL_MARINHA:  # Check if channel exists
            marinhaStatus = self.get_status_message(marinhaDownCounter, "MARINHA")
            if self.poliswag.VOICE_CHANNEL_MARINHA.name != marinhaStatus:
                try:
                    await self.poliswag.VOICE_CHANNEL_MARINHA.edit(name=marinhaStatus)
                except Exception as e:
                    print(f"Error editing Marinha channel: {e}")

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
                return f"{region}: 🔴 ({downCounter})"
            else:
                return f"{region}: 🔴"

    async def is_quest_scanning_complete(self):
        questScanningOngoing = self.poliswag.db.get_data_from_database(
            f"SELECT scanned FROM poliswag;"
        )
        isQuestScanning = True if questScanningOngoing["scanned"] == 0 else False
        if not isQuestScanning:
            return {"leiriaCompleted": False, "marinhaCompleted": False}

        try:
            responseLeiria = requests.get(self.LEIRIA_QUEST_SCANNING_URL)
            responseMarinha = requests.get(self.MARINHA_QUEST_SCANNING_URL)

            responseLeiria.raise_for_status()
            responseMarinha.raise_for_status()

            leiriaData = responseLeiria.json()
            marinhaData = responseMarinha.json()

            leiriaCompleted = leiriaData.get("total") == leiriaData.get("ar_quests")
            marinhaCompleted = marinhaData.get("total") == marinhaData.get("ar_quests")

            if leiriaCompleted and marinhaCompleted:
                self.poliswag.scanner_manager.update_quest_scanning_state()

            return {
                "leiriaCompleted": leiriaCompleted,
                "marinhaCompleted": marinhaCompleted,
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
                    accountStats = await response.json()

                    goodAccounts = accountStats.get("good", 0)
                    inUseAccounts = accountStats.get("in_use", 0)
                    cooldownAccounts = accountStats.get("cooldown", 0)
                    disabledAccounts = accountStats.get("disabled", 0)

                    return {
                        "good": goodAccounts,
                        "in_use": inUseAccounts,
                        "cooldown": cooldownAccounts + disabledAccounts,
                    }
            except Exception as e:
                print(f"Error encountered getting stats: {e}")
                return None
