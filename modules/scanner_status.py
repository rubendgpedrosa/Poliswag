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
            "LeiriaBigger": 4,
            "MarinhaGrande": 1,
        }

        self.last_all_down_request_time = 0
        self.UPDATE_THRESHOLD = 3600  # 1 hour
        self.ALL_DOWN_REQUEST_COOLDOWN = 900  # 15 minutes
        self.STALE_POKEMON_THRESHOLD = 600  # 10 min — matches worker liveness window

        # Quest-scan completion is detected by a plateau in the valid-quest count:
        # once the count stops growing for PLATEAU_TICKS consecutive minute-checks
        # (and is past COMPLETION_FLOOR of the adaptive expected total, with the
        # scanner alive), the scan is considered finished. See is_quest_scanning_complete.
        self.PLATEAU_TICKS = 10  # ~10 min; the scheduled loop runs every 60s
        self.COMPLETION_FLOOR = (
            0.90  # fraction of expected total before plateau may fire
        )
        # Self-heal escape hatch: the expected total only adapts (downward) once a
        # scan completes, but a shrunk map (reduced radius / removed pokestops) can
        # leave the live ceiling permanently below COMPLETION_FLOOR of a stale
        # expected — deadlocking completion forever. A plateau that holds this much
        # longer is accepted as the new ceiling regardless of the floor, which lets
        # record_quest_scan_completion re-baseline the expected. Still gated by the
        # scanner-alive check, so a crash-induced stall never self-completes.
        self.STUCK_TICKS = 30  # ~30 min flat below the floor => treat as new ceiling
        self.DEFAULT_EXPECTED_TOTALS = {"leiria": 371, "marinha": 109}
        self._quest_plateau = {
            "leiria": {"prev_count": -1, "flat_streak": 0},
            "marinha": {"prev_count": -1, "flat_streak": 0},
        }

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

        # The red state alone is ambiguous: workers can be down because the
        # MITM device dropped off Rotom, or because the device is fine but the
        # account pool is exhausted. Only when a region would show red do we
        # pay for the device lookup, so ❌ (device offline) can be told apart
        # from 🔴 (device up, accounts/workers down).
        device_connected = True
        indicators = (
            self._get_status_indicator(leiriaDownCounter, "LEIRIA"),
            self._get_status_indicator(marinhaDownCounter, "MARINHA"),
        )
        if "🔴" in indicators:
            device_connected = await self.poliswag.account_monitor.is_device_connected()

        # Fully red — regardless of the device flag (❌ is only a display
        # distinction) — feeds the StackRecovery ladder: containers first,
        # device reboot if red persists.
        all_red = indicators == ("🔴", "🔴")
        await self.poliswag.stack_recovery.observe(all_red)

        for channel_key, counter, region in [
            ("leiria", leiriaDownCounter, "LEIRIA"),
            ("marinha", marinhaDownCounter, "MARINHA"),
        ]:
            if self.should_update_channel(channel_key, counter, device_connected):
                status = self.get_status_message(counter, region, device_connected)
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

    def should_update_channel(self, channelType, counter, device_connected=True):
        current_time = time.time()
        cacheEntry = self.channelCache[channelType]

        if (
            cacheEntry["name"] is None
            or current_time - cacheEntry["last_update"] >= self.UPDATE_THRESHOLD
        ):
            return True

        new_status = self.get_status_message(
            counter, channelType.upper(), device_connected
        )
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

    def _get_status_indicator(self, downCounter, region):
        if downCounter is None:
            return "❓"

        if region == "MARINHA":
            return "🟢" if downCounter == 0 else "🔴"

        expected_workers = self.defaultExpectedWorkers.get("LeiriaBigger", 5)
        down_percentage = (
            (downCounter / expected_workers) if expected_workers > 0 else 0
        )

        if down_percentage == 0:
            return "🟢"
        elif down_percentage <= 0.4:
            return "🟡"
        elif down_percentage <= 0.8:
            return "🟠"
        return "🔴"

    def get_status_message(self, downCounter, region, device_connected=True):
        status_indicator = self._get_status_indicator(downCounter, region)
        # ❌ only ever replaces red: a healthy-looking region stays green even
        # if the device flag momentarily reads disconnected.
        if status_indicator == "🔴" and not device_connected:
            status_indicator = "❌"
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
            # RotomNG renamed the device fields to snake_case
            # (last_seen_at_ms / id / is_connected); fall back to the legacy
            # Node Rotom names so the bot works against either backend.
            last_ms = d.get("last_seen_at_ms", d.get("dateLastMessageReceived", 0))
            last_sec = (now * 1000 - last_ms) / 1000 if last_ms else None
            devices.append(
                {
                    "origin": d.get("origin", d.get("id", d.get("deviceId", "?"))),
                    "is_alive": d.get("is_connected", d.get("isAlive", False)),
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
            leiria_scanned = self._count_valid_quests(db, leiria=True)
            marinha_scanned = self._count_valid_quests(db, leiria=False)
            if leiria_scanned is None or marinha_scanned is None:
                return None

            expected_leiria, expected_marinha = self._get_expected_totals()

            leiria_plateaued = self._update_plateau(
                "leiria", leiria_scanned, expected_leiria
            )
            marinha_plateaued = self._update_plateau(
                "marinha", marinha_scanned, expected_marinha
            )

            # The scanner-alive gate only matters once both areas have plateaued;
            # checking it lazily avoids a worker-status fetch on every tick.
            scanner_alive = (
                await self._is_scanner_alive()
                if (leiria_plateaued and marinha_plateaued)
                else False
            )

            return {
                "leiriaCompleted": leiria_plateaued and scanner_alive,
                "marinhaCompleted": marinha_plateaued and scanner_alive,
                "leiriaTotal": expected_leiria,
                "leiriaScanned": leiria_scanned,
                "marinhaTotal": expected_marinha,
                "marinhaScanned": marinha_scanned,
                "leiriaPercentage": self._coverage_pct(leiria_scanned, expected_leiria),
                "marinhaPercentage": self._coverage_pct(
                    marinha_scanned, expected_marinha
                ),
            }

        except Exception as e:
            self._log(f"Error in quest scanning check: {e}")
            return None

    def _count_valid_quests(self, db, *, leiria):
        """Count pokestops in an area whose quest (AR or standard) is still valid.

        The area split is by longitude (Marinha Grande is at or west of
        ``_MARINHA_LON_MAX``). Unlike the old logic this does NOT restrict the
        universe to stops that already carry quest data — it just counts the
        live quests, which climbs from 0 toward the area's natural ceiling as the
        scan progresses. Returns ``None`` if the query yields no row.
        """
        op = ">" if leiria else "<="
        rows = db.get_data_from_database(
            f"""
            SELECT COALESCE(SUM(CASE WHEN quest_expiry > UNIX_TIMESTAMP()
                                       OR alternative_quest_expiry > UNIX_TIMESTAMP()
                                     THEN 1 ELSE 0 END), 0) AS scanned
            FROM pokestop WHERE deleted = 0 AND lon {op} %s
            """,
            params=(_MARINHA_LON_MAX,),
        )
        if not rows:
            return None
        # SUM(CASE ...) comes back as decimal.Decimal; coerce to int so derived
        # percentages stay plain floats (a Decimal here crashes the progress
        # embed's Decimal + float arithmetic).
        return int(rows[0]["scanned"] or 0)

    def _coverage_pct(self, count, expected):
        """Percentage of the expected total covered, capped at 100%."""
        if expected <= 0:
            return 0
        return min(count / expected * 100, 100)

    def _update_plateau(self, area, count, expected):
        """Advance the plateau tracker for an area and report whether it is done.

        An area is done when its valid-quest count has stopped growing for
        ``PLATEAU_TICKS`` consecutive checks, is non-zero, and has reached at
        least ``COMPLETION_FLOOR`` of the expected total (the floor blocks a
        false "done" from an early stall below the natural ceiling).
        """
        state = self._quest_plateau[area]
        if count > state["prev_count"]:
            state["prev_count"] = count
            state["flat_streak"] = 0
        elif count == state["prev_count"]:
            state["flat_streak"] += 1
        else:
            # Count dropped (e.g. quests expiring at day rollover) — restart.
            state["prev_count"] = count
            state["flat_streak"] = 0

        if count <= 0:
            return False
        reached_floor = (
            state["flat_streak"] >= self.PLATEAU_TICKS
            and count >= expected * self.COMPLETION_FLOOR
        )
        # Self-heal: a much longer flat plateau below the floor is the real
        # (shrunk) ceiling, not an early stall — accept it so the expected total
        # can re-baseline. See STUCK_TICKS.
        stuck_below_floor = state["flat_streak"] >= self.STUCK_TICKS
        return reached_floor or stuck_below_floor

    async def _is_scanner_alive(self):
        """True when at least one expected worker is executing across the areas.

        Guards against announcing completion while the scanner is dark: a full
        outage (or an unreachable status endpoint → ``None`` counters) reports
        not-alive, so a plateau caused by a crash never fires as "done".
        """
        workers = await self.get_workers_with_issues()
        down_leiria = workers["downDevicesLeiria"]
        down_marinha = workers["downDevicesMarinha"]
        leiria_alive = (
            down_leiria is not None
            and down_leiria < self.defaultExpectedWorkers.get("LeiriaBigger", 4)
        )
        marinha_alive = (
            down_marinha is not None
            and down_marinha < self.defaultExpectedWorkers.get("MarinhaGrande", 1)
        )
        return leiria_alive or marinha_alive

    def _get_expected_totals(self):
        """Read the adaptive per-area expected totals from the poliswag table.

        Falls back to the seeded defaults if the columns are missing/NULL or the
        query fails, so completion detection keeps working pre-migration.
        """
        try:
            rows = self.poliswag.db.get_data_from_database(
                "SELECT quest_expected_leiria, quest_expected_marinha FROM poliswag"
            )
            if rows:
                return (
                    rows[0].get("quest_expected_leiria")
                    or self.DEFAULT_EXPECTED_TOTALS["leiria"],
                    rows[0].get("quest_expected_marinha")
                    or self.DEFAULT_EXPECTED_TOTALS["marinha"],
                )
        except Exception as e:
            self._log(f"Error reading expected quest totals: {e}")
        return (
            self.DEFAULT_EXPECTED_TOTALS["leiria"],
            self.DEFAULT_EXPECTED_TOTALS["marinha"],
        )

    def record_quest_scan_completion(self, leiria_count, marinha_count):
        """Persist the observed finish counts as the new expected totals and
        reset the in-memory plateau trackers. Called once a scan completes so the
        floor adapts as pokestops are added or removed over time."""
        try:
            self.poliswag.db.execute_query_to_database(
                "UPDATE poliswag SET quest_expected_leiria = %s, quest_expected_marinha = %s",
                params=(leiria_count, marinha_count),
            )
        except Exception as e:
            self._log(f"Error updating expected quest totals: {e}")
        self.reset_quest_plateau()

    def reset_quest_plateau(self):
        """Clear plateau tracking so the next scan cycle starts fresh."""
        for state in self._quest_plateau.values():
            state["prev_count"] = -1
            state["flat_streak"] = 0
