import datetime
import time
import discord
from modules.http_client import fetch_data
from modules.config import Config

_MARINHA_LON_MAX = -8.9  # pokestops at or west of this longitude are in Marinha Grande


class ScannerStatus:
    def __init__(self, poliswag):
        self.poliswag = poliswag

        self.channelCache = {
            "leiria": {"name": None, "last_update": 0},
            "marinha": {"name": None, "last_update": 0},
        }

        self.defaultExpectedWorkers = {
            "Leiria": 4,
            "MarinhaGrande": 1,
        }

        self.last_all_down_request_time = 0
        self.UPDATE_THRESHOLD = 3600  # 1 hour
        self.ALL_DOWN_REQUEST_COOLDOWN = 900  # 15 minutes
        self.STALE_POKEMON_THRESHOLD = 600  # 10 min — matches worker liveness window

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

        # Webhook fires only when pokemon data has gone stale — worker count
        # alone is not a reliable signal (workers can appear "running" but stuck).
        seconds_since_pokemon = self._get_seconds_since_last_pokemon()
        pokemon_stale = (
            seconds_since_pokemon is not None
            and seconds_since_pokemon >= self.STALE_POKEMON_THRESHOLD
        )
        if pokemon_stale:
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

                if areaName == "Leiria":
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

    def _get_seconds_since_last_pokemon(self) -> int | None:
        """Return how many seconds have passed since the newest pokemon row was updated."""
        try:
            rows = self.poliswag.quest_search.db.get_data_from_database(
                "SELECT UNIX_TIMESTAMP() - MAX(updated) AS seconds_ago FROM pokemon"
            )
            if rows and rows[0]["seconds_ago"] is not None:
                return int(rows[0]["seconds_ago"])
        except Exception as e:
            self._log(f"Error querying last pokemon timestamp: {e}")
        return None

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
                seconds_ago = self._get_seconds_since_last_pokemon()
                if seconds_ago is not None:
                    last_pokemon_msg = f"Last pokemon scanned {seconds_ago}s ago"
                else:
                    last_pokemon_msg = "Last pokemon scan time unknown"
                payload = {
                    "type": "map_status",
                    "value": {
                        "accounts": account_data.get("good"),
                        "device_status": device_status,
                        "last_pokemon_seconds_ago": seconds_ago,
                        "last_pokemon_message": last_pokemon_msg,
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
            expected_workers = self.defaultExpectedWorkers.get("Leiria", 5)
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

    async def get_full_status(self) -> dict:
        """Collect a diagnostic snapshot from all scanner sources.

        Returns a dict with keys:
          last_pokemon_seconds_ago  int | None
          devices                   list[dict]   — from Rotom
          workers                   list[dict]   — from Dragonite
          accounts                  dict         — from Dragonite account pool
        """
        now = time.time()

        seconds_ago = self._get_seconds_since_last_pokemon()

        device_data = await fetch_data("device_status", log_fn=self._log) or {}
        raw_devices = device_data.get("devices", [])
        devices = []
        for d in raw_devices:
            last_ms = d.get("dateLastMessageReceived", 0)
            last_sec = (now * 1000 - last_ms) / 1000 if last_ms else None
            devices.append(
                {
                    "origin": d.get("origin", d.get("deviceId", "?")),
                    "is_alive": d.get("isAlive", False),
                    "last_msg_seconds_ago": (
                        int(last_sec) if last_sec is not None else None
                    ),
                }
            )

        scanner_data = await fetch_data("scanner_status", log_fn=self._log) or {}
        workers = []
        for area in scanner_data.get("areas", []):
            for wm in area.get("worker_managers", []):
                for w in wm.get("workers", []):
                    last_data = w.get("last_data")
                    age = int(now - last_data) if last_data else None
                    workers.append(
                        {
                            "worker_id": w.get("worker_id", "?"),
                            "area": area.get("name", "?"),
                            "status": w.get("connection_status", "?"),
                            "last_data_seconds_ago": age,
                        }
                    )

        accounts = await self.poliswag.account_monitor.get_account_stats()

        return {
            "last_pokemon_seconds_ago": seconds_ago,
            "devices": devices,
            "workers": workers,
            "accounts": accounts,
        }

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
            db = self.poliswag.quest_search.db
            leiria_rows = db.get_data_from_database(
                """
                SELECT COUNT(*) AS total,
                       COALESCE(SUM(CASE WHEN quest_expiry > UNIX_TIMESTAMP() THEN 1 ELSE 0 END), 0) AS scanned
                FROM pokestop WHERE deleted = 0 AND lon > %s AND quest_timestamp IS NOT NULL
                """,
                params=(_MARINHA_LON_MAX,),
            )
            marinha_rows = db.get_data_from_database(
                """
                SELECT COUNT(*) AS total,
                       COALESCE(SUM(CASE WHEN quest_expiry > UNIX_TIMESTAMP() THEN 1 ELSE 0 END), 0) AS scanned
                FROM pokestop WHERE deleted = 0 AND lon <= %s AND quest_timestamp IS NOT NULL
                """,
                params=(_MARINHA_LON_MAX,),
            )

            if not leiria_rows or not marinha_rows:
                return None

            leiria_total = leiria_rows[0]["total"] or 0
            leiria_scanned = leiria_rows[0]["scanned"] or 0
            marinha_total = marinha_rows[0]["total"] or 0
            marinha_scanned = marinha_rows[0]["scanned"] or 0

            if leiria_scanned == 0 or marinha_scanned == 0:
                return None

            leiria_percentage = (
                (leiria_scanned / leiria_total * 100) if leiria_total > 0 else 0
            )
            marinha_percentage = (
                (marinha_scanned / marinha_total * 100) if marinha_total > 0 else 0
            )

            leiria_threshold = leiria_total * 0.98
            marinha_threshold = marinha_total * 0.98
            return {
                "leiriaCompleted": leiria_scanned >= leiria_threshold,
                "marinhaCompleted": marinha_scanned >= marinha_threshold,
                "leiriaTotal": leiria_total,
                "leiriaScanned": leiria_scanned,
                "marinhaTotal": marinha_total,
                "marinhaScanned": marinha_scanned,
                "leiriaPercentage": leiria_percentage,
                "marinhaPercentage": marinha_percentage,
            }

        except Exception as e:
            self._log(f"Error in quest scanning check: {e}")
            return None
