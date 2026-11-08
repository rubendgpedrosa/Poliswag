import asyncio
import collections
import datetime
import time
import discord
from modules.http_client import fetch_data
from modules.config import Config
from modules import scan_areas
from modules.logging_mixin import LoggingMixin
from modules.owner_alert import notify_owner


class ScannerStatus(LoggingMixin):
    def __init__(self, poliswag):
        self.poliswag = poliswag

        # The status-channel name we last successfully wrote. Compared on
        # every tick so an unchanged status costs zero Discord calls. Reset to
        # None by a restart, so the first tick of a new process always renames
        # once — accepted as cheap next to a per-tick name lookup.
        self.channelStatusName: str | None = None

        # Discord allows two channel renames per 10 minutes. Past that it
        # answers 429 with a ~600s retry_after, and discord.py sleeps it out
        # inside channel.edit() -- which stalled the whole 60s scheduler tick
        # for 10 min at a time while a flapping device kept changing the
        # status. So the budget is tracked here and a rename that would
        # exceed it is skipped; the next tick with budget writes whatever the
        # status is by then. RENAME_TIMEOUT is the backstop for a 429 the
        # local count didn't foresee (e.g. renames spent by a previous
        # process just before a restart).
        self.RENAME_BUDGET = 2
        self.RENAME_WINDOW = 600
        self.RENAME_TIMEOUT = 15
        self._rename_attempts: collections.deque[float] = collections.deque()

        self.last_all_down_request_time = 0
        # Keep recovery pending until the owner has received the recovery DM.
        # Separate flags stop retries from repeating successful deliveries.
        self.map_down_alerted = False
        self._map_down_discord_sent = False
        self._map_restored_dashboard_sent = False
        self.ALL_DOWN_REQUEST_COOLDOWN = 900  # 15 minutes
        self.STALE_POKEMON_THRESHOLD = 600  # 10 min — matches worker liveness window

        # Quest-scan completion is detected by a plateau in the valid-quest count:
        # once the count stops growing for PLATEAU_TICKS consecutive minute-checks
        # (and is past COMPLETION_FLOOR of the area's live pokestop total, with the
        # scanner alive), the scan is considered finished. See is_quest_scanning_complete.
        # The total is counted live from the scanner database on every check, in the
        # same query as the quests, so a changed fence or a new area is right at once.
        self.PLATEAU_TICKS = 10  # ~10 min; the scheduled loop runs every 60s
        self.COMPLETION_FLOOR = (
            0.90  # fraction of the live total before plateau may fire
        )
        # Never-stuck guards. Some stops never get a quest (out of reach, no
        # quest that day), so an area can top out below COMPLETION_FLOOR: a
        # plateau that holds this much longer is accepted as that area's real
        # ceiling. Still gated by the scanner-alive check, so a crash-induced
        # stall never self-completes.
        self.STUCK_TICKS = 30  # ~30 min flat below the floor => accept the ceiling
        # An area that stays at zero quests this long, while another area
        # already has quests, is not being quest-scanned at all (left in the
        # registry but off in the scanner); it must not hold the others back.
        self.ZERO_STUCK_TICKS = 120  # ~2 h at 0 while the rest of the map has quests
        # Right after a plateau reset (day-change, or a bot restart shortly after
        # midnight) yesterday's pokestop.quest_expiry rows can still read as valid
        # for a bit — the external scanner takes real time to clear/re-scan them.
        # Ignore plateau signals for this long after any reset so a flat, stale
        # leftover count from yesterday can never be mistaken for today's scan.
        self.PLATEAU_RESET_QUIET_PERIOD = 120  # seconds
        # Per-area plateau state, keyed by the registry's area_key (created on
        # first use, so activating an area needs no restart).
        self._quest_plateau: dict[str, dict] = {}
        # Also starts the quiet period on bot startup — a restart shortly after
        # midnight must not trust a plateau signal any sooner than a live reset.
        self._plateau_reset_at = time.time()

    async def get_voice_channel(self):
        try:
            channel_id = Config.VOICE_CHANNEL_ID
            if not channel_id:
                self._log("No voice channel configured for the combined map status")
                return None
            # discord.py caches every channel the bot can see, so the
            # normal path costs no HTTP at all. fetch_channel is only the
            # fallback for a cache miss (e.g. a tick that lands before the
            # gateway READY has populated the cache).
            cached = self.poliswag.get_channel(channel_id)
            if cached is not None:
                return cached
            return await self.poliswag.fetch_channel(channel_id)
        except Exception as e:
            self._log(f"Error fetching status channel: {e}")
            return None

    async def rename_voice_channels(self, workers_status):
        down_total = workers_status.get("downDevicesTotal")
        expected_total = workers_status.get("expectedWorkersTotal")

        # Webhook fires only when pokemon data has gone stale — worker count
        # alone is not a reliable signal (workers can appear "running" but stuck).
        seconds_since_pokemon = await self._get_seconds_since_last_pokemon()
        pokemon_stale = (
            seconds_since_pokemon is not None
            and seconds_since_pokemon >= self.STALE_POKEMON_THRESHOLD
        )
        if pokemon_stale:
            await self.trigger_all_down_action()
        elif self.map_down_alerted and seconds_since_pokemon is not None:
            await self.trigger_map_restored_action(seconds_since_pokemon)

        # Every active area shares one device and one stack, so a single
        # combined indicator (down workers / total expected workers across all
        # active areas, both read live from Dragonite rather than a hardcoded
        # guess) is more honest than several labels that would always move in
        # lockstep anyway. Only 🟢 (scanning) and 🔴 (every worker down) come
        # out of it. Either input being None (Dragonite unreachable, or an
        # active area missing from its response) makes this None too — see
        # _get_combined_status_indicator.
        indicator = self._get_combined_status_indicator(down_total, expected_total)

        # The red state alone is ambiguous: every worker can be down because
        # the MITM device dropped off Rotom, or because the device is fine but
        # the account pool is exhausted. Only when it would show red do we pay
        # for the device lookup, so ❌ (device offline) can be told apart
        # from 🔴 (device up, all accounts down).
        device_connected = True
        if indicator == "🔴":
            device_connected = await self.poliswag.account_monitor.is_device_connected()

        # Dragonite's view (None when it didn't answer) plus the Aegis workers
        # rotom-ng has (None likewise) are what StackRecovery sorts into
        # "restart the phone", "recreate the stack" or "just wait".
        all_red: bool | None = None if indicator is None else indicator == "🔴"
        workers = await self.poliswag.account_monitor.connected_worker_count()
        await self.poliswag.stack_recovery.observe(all_red, workers)

        status = self.get_status_message(down_total, expected_total, device_connected)
        if self.should_update_channel(status) and self._rename_budget_available():
            channel = await self.get_voice_channel()
            if channel:
                self._rename_attempts.append(time.monotonic())
                try:
                    await asyncio.wait_for(
                        channel.edit(name=status), timeout=self.RENAME_TIMEOUT
                    )
                    self.channelStatusName = status
                except asyncio.TimeoutError:
                    # Almost certainly a 429 discord.py was waiting out. Mark
                    # the whole window spent so no tick retries into it.
                    now = time.monotonic()
                    self._rename_attempts.extend([now] * self.RENAME_BUDGET)
                    self._log(
                        f"Status channel rename to {status!r} timed out "
                        "(rate limited); retrying once the rename budget frees up",
                        "INFO",
                    )
                except discord.errors.HTTPException as e:
                    if e.code == 429:
                        self._log(f"Rate limited while updating status channel: {e}")
                    else:
                        self._log(f"Error updating status channel: {e}")

    def _rename_budget_available(self):
        """True while fewer than RENAME_BUDGET renames were attempted in the
        last RENAME_WINDOW seconds. Attempts count, not successes: a 429'd
        PATCH still spends Discord's budget."""
        cutoff = time.monotonic() - self.RENAME_WINDOW
        while self._rename_attempts and self._rename_attempts[0] <= cutoff:
            self._rename_attempts.popleft()
        return len(self._rename_attempts) < self.RENAME_BUDGET

    def should_update_channel(self, new_status):
        """True only when the channel name would actually change.

        Discord allows two channel renames per 10 minutes, so a tick that
        agrees with what we last wrote must cost zero API calls. There is
        deliberately no periodic re-assert: rewriting a name that is already
        correct just spends the rename budget a real status change needs.
        """
        return new_status != self.channelStatusName

    async def get_workers_with_issues(self):
        """Down/expected worker counts per active registry area.

        Dragonite is the source: each area in its /status response is matched
        to the registry row whose dragonite_area has that name. A registry
        area missing from the answer (or Dragonite unreachable) stays None, so
        the combined indicator refuses to guess. The totals are None too when
        any active area's counts are unknown.
        """
        await scan_areas.ensure()
        workerStatus = await fetch_data("scanner_status", log_fn=self._log)
        rows = scan_areas.active_areas()
        down_by_area = {row["area_key"]: None for row in rows}
        expected_by_area = {row["area_key"]: None for row in rows}

        if workerStatus and "areas" in workerStatus:
            by_name = {
                row["dragonite_area"].strip().casefold(): row["area_key"]
                for row in rows
            }
            for area in workerStatus["areas"]:
                area_name = str(area.get("name") or "").strip().casefold()
                key = by_name.get(area_name)
                if key is None:
                    continue

                managers = area.get("worker_managers") or []
                expectedWorkers = None
                if managers:
                    expectedWorkers = managers[0].get("expected_workers")
                if expectedWorkers is None:
                    # A response without expected_workers: assume every worker
                    # it lists was expected, so a missing field cannot look
                    # like a healthy pool.
                    expectedWorkers = sum(
                        len(manager.get("workers") or []) for manager in managers
                    )

                downDevices = expectedWorkers
                for workerManager in managers:
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

                # Sum, don't replace: Dragonite has kept a renamed predecessor
                # area around before, and reading one name made a partial
                # outage look like the whole map was down.
                down = max(downDevices, 0)
                down_by_area[key] = (down_by_area[key] or 0) + down
                expected_by_area[key] = (expected_by_area[key] or 0) + expectedWorkers

        if not rows or any(value is None for value in down_by_area.values()):
            down_total = None
            expected_total = None
        else:
            down_total = sum(down_by_area.values())
            expected_total = sum(expected_by_area.values())

        return {
            "downDevices": down_by_area,
            "expectedWorkers": expected_by_area,
            "downDevicesTotal": down_total,
            "expectedWorkersTotal": expected_total,
        }

    async def _get_seconds_since_last_pokemon(self) -> int | None:
        """Return how many seconds have passed since the newest pokemon row was updated."""
        try:
            rows = await self.poliswag.quest_search.db.get_data_from_database(
                "SELECT UNIX_TIMESTAMP() - MAX(updated) AS seconds_ago FROM pokemon"
            )
            if rows and rows[0]["seconds_ago"] is not None:
                return int(rows[0]["seconds_ago"])
        except Exception as e:
            self._log(f"Error querying last pokemon timestamp: {e}")
        return None

    async def _get_iv_verification_stats(self, minutes: int = 10) -> dict | None:
        """Scanner throughput/IV-verification over the last `minutes`,
        across every active area, from golbat's per-minute
        pokemon_area_stats snapshots. None on a query failure or no rows
        (e.g. right after a fresh restart before any snapshot has landed)."""
        names = await self._stats_area_names()
        if not names:
            return None
        placeholders = ", ".join(["%s"] * len(names))
        try:
            rows = await self.poliswag.quest_search.db.get_data_from_database(
                "SELECT COALESCE(SUM(totMon), 0) AS total, "
                "COALESCE(SUM(ivMon), 0) AS iv, "
                "COALESCE(SUM(unverifiedEnc), 0) AS unverified "
                "FROM pokemon_area_stats "
                "WHERE datetime >= UNIX_TIMESTAMP() - %s "
                f"AND area IN ({placeholders})",
                params=(minutes * 60, *names),
            )
            if rows and int(rows[0]["total"]) > 0:
                return {
                    "total": int(rows[0]["total"]),
                    "iv": int(rows[0]["iv"]),
                    "unverified": int(rows[0]["unverified"]),
                }
        except Exception as e:
            self._log(f"Error querying IV verification stats: {e}")
        return None

    async def _stats_area_names(self) -> list[str]:
        """Golbat's area names for the active registry areas, in display order."""
        await scan_areas.ensure()
        return scan_areas.dragonite_area_names()

    async def get_iv_verification_by_area(self, minutes: int = 60) -> dict:
        """Per-area scanner IV-verification over the last `minutes`, from the
        same pokemon_area_stats snapshots as _get_iv_verification_stats,
        keyed by the area name golbat reports. Areas with no activity in the
        window are omitted; empty dict on a query failure.

        Defaults to an hour rather than the 10 minutes _get_iv_verification_
        stats uses: this feeds the account card, which labels the figure
        "spawns/h" -- a 10-minute sample was both noisier and easy to
        misread as a running total. Keep this in sync with that label.
        """
        names = await self._stats_area_names()
        if not names:
            return {}
        placeholders = ", ".join(["%s"] * len(names))
        try:
            rows = await self.poliswag.quest_search.db.get_data_from_database(
                "SELECT area, COALESCE(SUM(totMon), 0) AS total, "
                "COALESCE(SUM(ivMon), 0) AS iv "
                "FROM pokemon_area_stats "
                "WHERE datetime >= UNIX_TIMESTAMP() - %s "
                f"AND area IN ({placeholders}) "
                "GROUP BY area",
                params=(minutes * 60, *names),
            )
            return {
                row["area"]: {"total": int(row["total"]), "iv": int(row["iv"])}
                for row in (rows or [])
                if int(row["total"]) > 0
            }
        except Exception as e:
            self._log(f"Error querying per-area IV verification stats: {e}")
        return {}

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
                seconds_ago = await self._get_seconds_since_last_pokemon()
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
                    # HA updates the dashboard independently of Discord delivery.
                    self.map_down_alerted = True
                    self._map_restored_dashboard_sent = False
                    try:
                        await fetch_data(
                            "all_down", log_fn=self._log, method="POST", data=payload
                        )
                    except Exception as e:
                        self._log(f"Error sending all-down dashboard update: {e}")
                    if not self._map_down_discord_sent:
                        try:
                            self._map_down_discord_sent = await notify_owner(
                                self.poliswag,
                                "O mapa deixou de receber Pokémon há pelo menos "
                                "10 minutos. Vou avisar quando voltar a receber dados.",
                                title="Mapa sem dados",
                                tag="poliswag-map",
                            )
                        except Exception as e:
                            self._log(f"Error sending map-down Discord alert: {e}")
                self.map_down_alerted = True
            except Exception as e:
                self._log(f"Error sending all-down notification: {e}")

    async def trigger_map_restored_action(self, seconds_ago):
        """The other half of the all-down alert: say once that spawns flow again."""
        payload = {
            "type": "map_restored",
            "value": {"last_pokemon_seconds_ago": seconds_ago},
        }
        if not Config.IS_PRODUCTION:
            self._log(
                f"[DEV] Would send map-restored notification with payload: {payload}",
                "INFO",
            )
            self.map_down_alerted = False
            return
        if not self._map_restored_dashboard_sent:
            try:
                await fetch_data(
                    "all_down", log_fn=self._log, method="POST", data=payload
                )
            except Exception as e:
                self._log(f"Error sending map-restored notification: {e}")
            self._map_restored_dashboard_sent = True
        if self._map_down_discord_sent:
            try:
                delivered = await notify_owner(
                    self.poliswag,
                    "O mapa voltou a receber Pokémon.",
                    title="Mapa recuperado",
                    tag="poliswag-map",
                )
            except Exception as e:
                self._log(f"Error sending map-restored Discord alert: {e}")
                return
            if not delivered:
                return
            self._map_down_discord_sent = False
        self.map_down_alerted = False

    def _get_combined_status_indicator(self, down_total, expected_total):
        # Any missing input — Dragonite unreachable, or an active area absent
        # from its response — means we can't compute a percentage at all.
        # Return None (rather than guessing) so the caller can render it as
        # down without treating it as a confirmed red reading — see
        # get_status_message and rename_voice_channels.
        if down_total is None or expected_total is None:
            return None

        # Only two computed states: the map is either scanning or it is not.
        # A partially-down worker pool still produces a scanned map, so
        # anything short of every worker being down reads green. The old
        # 🟡/🟠 tiers renamed the status channel on every swing in the
        # account pool — churn against Discord's channel-rename rate limit
        # for a distinction nobody acted on.
        if expected_total > 0 and down_total >= expected_total:
            return "🔴"
        return "🟢"

    def get_status_message(
        self,
        down_total,
        expected_total,
        device_connected=True,
    ):
        status_indicator = self._get_combined_status_indicator(
            down_total, expected_total
        )
        # Three states only — 🟢 scanning, 🔴 every account/worker down with
        # the device still connected, ❌ device offline (or no data at all).
        # No data (Dragonite unreachable, or an area missing from its
        # response) still reads as down from an operator's point of view —
        # show ❌ rather than a wishy-washy "unknown". This is a display
        # choice only: rename_voice_channels keys all_red off the raw
        # indicator, not this string, so a data outage never triggers the
        # recovery ladder even though it looks the same as a confirmed
        # device-offline red.
        if status_indicator is None:
            return "STATUS: ❌"
        if status_indicator == "🔴" and not device_connected:
            status_indicator = "❌"
        return f"STATUS: {status_indicator}"

    async def get_full_status(self) -> dict:
        """Collect a diagnostic snapshot from all scanner sources.

        Returns a dict with keys:
          last_pokemon_seconds_ago  int | None
          devices                   list[dict]   — from Rotom
          workers                   list[dict]   — from Dragonite
          accounts                  dict         — from Dragonite account pool
          iv_verification           dict | None  — scanner throughput, last 10 min
        """
        now = time.time()

        seconds_ago = await self._get_seconds_since_last_pokemon()
        iv_verification = await self._get_iv_verification_stats()

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
            "iv_verification": iv_verification,
        }

    async def is_quest_scanning_complete(self):
        if time.time() - self._plateau_reset_at < self.PLATEAU_RESET_QUIET_PERIOD:
            return None

        quest_scanning_ongoing = await self.poliswag.db.get_data_from_database(
            "SELECT scanned FROM poliswag WHERE scanned = 1;"
        )

        if quest_scanning_ongoing and len(quest_scanning_ongoing) > 0:
            return None

        try:
            await scan_areas.ensure()
            areas = scan_areas.active_areas()
            if not areas:
                return None
            db = self.poliswag.quest_search.db
            counted = {
                row["area_key"]: await self._count_area(db, row) for row in areas
            }
            if any(pair is None for pair in counted.values()):
                return None
            scanned = {key: pair[0] for key, pair in counted.items()}
            expected = {key: pair[1] for key, pair in counted.items()}

            others_have_quests = {
                key: any(n > 0 for other, n in scanned.items() if other != key)
                for key in scanned
            }
            plateaued = {
                key: self._update_plateau(
                    key, count, expected[key], others_have_quests[key]
                )
                for key, count in scanned.items()
            }

            # The scanner-alive gate only matters once every area has
            # plateaued; checking it lazily avoids a worker-status fetch on
            # every tick.
            scanner_alive = (
                await self._is_scanner_alive() if all(plateaued.values()) else False
            )

            return {
                "areas": {
                    row["area_key"]: {
                        "completed": plateaued[row["area_key"]] and scanner_alive,
                        "total": expected.get(row["area_key"], 0),
                        "scanned": scanned[row["area_key"]],
                        "percentage": self._coverage_pct(
                            scanned[row["area_key"]],
                            expected.get(row["area_key"], 0),
                        ),
                    }
                    for row in areas
                },
                "completed": all(plateaued.values()) and scanner_alive,
            }

        except Exception as e:
            self._log(f"Error in quest scanning check: {e}")
            return None

    async def _count_area(self, db, area):
        """Return ``(stops with a valid quest, all stops)`` for an area.

        The area is its registry fence: the query is pre-filtered by the
        fence's bounding box, then each row is classified by point-in-polygon,
        so a stop in a neighbouring area is never counted here. Both numbers
        come from the same rows, so the total is always the live one: a
        changed fence or a newly activated area needs no stored value and no
        recount. Returns ``None`` when the area has no usable fence or the
        query fails.
        """
        box = scan_areas.area_bbox(area["area_key"])
        if box is None:
            return None
        rows = await db.get_data_from_database(
            """
            SELECT lat, lon,
                   CASE WHEN quest_expiry > UNIX_TIMESTAMP()
                          OR alternative_quest_expiry > UNIX_TIMESTAMP()
                        THEN 1 ELSE 0 END AS scanned
            FROM pokestop
            WHERE deleted = 0 AND lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s
            """,
            params=(box[0], box[2], box[1], box[3]),
        )
        if rows is None:
            return None
        scanned = total = 0
        for row in rows:
            if scan_areas.area_for(row["lat"], row["lon"]) != area["area_key"]:
                continue
            total += 1
            if int(row.get("scanned") or 0):
                scanned += 1
        return scanned, total

    def _coverage_pct(self, count, expected):
        """Percentage of the expected total covered, capped at 100%."""
        if expected <= 0:
            return 0
        return min(count / expected * 100, 100)

    def _update_plateau(self, area, count, expected, others_have_quests=False):
        """Advance the plateau tracker for an area and report whether it is done.

        An area is done when its valid-quest count has stopped growing for
        ``PLATEAU_TICKS`` consecutive checks, is non-zero, and has reached at
        least ``COMPLETION_FLOOR`` of the area's live total (the floor blocks a
        false "done" from an early stall below the natural ceiling). The
        never-stuck guards below make sure every area eventually reports done
        while the scanner is alive.
        """
        state = self._quest_plateau.setdefault(
            area, {"prev_count": -1, "flat_streak": 0}
        )
        if expected <= 0:
            # No stops inside the fence: nothing to scan, nothing to wait for.
            return True
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
            # Zero quests for hours while the rest of the map has them means
            # this area is not being quest-scanned; do not let it block.
            return others_have_quests and state["flat_streak"] >= self.ZERO_STUCK_TICKS
        reached_floor = (
            state["flat_streak"] >= self.PLATEAU_TICKS
            and expected > 0
            and count >= expected * self.COMPLETION_FLOOR
        )
        # A much longer flat plateau below the floor is the area's real
        # ceiling (stops that never get a quest), not an early stall.
        stuck_below_floor = state["flat_streak"] >= self.STUCK_TICKS
        return reached_floor or stuck_below_floor

    async def _is_scanner_alive(self):
        """True when at least one expected worker is executing across the areas.

        Guards against announcing completion while the scanner is dark: a full
        outage (or an unreachable status endpoint → ``None`` counters) reports
        not-alive, so a plateau caused by a crash never fires as "done".
        """
        workers = await self.get_workers_with_issues()
        down_total = workers["downDevicesTotal"]
        expected_total = workers["expectedWorkersTotal"]
        return (
            down_total is not None
            and expected_total is not None
            and down_total < expected_total
        )

    async def record_quest_scan_completion(self, counts):
        """Reset the plateau trackers once a scan completes. `counts` (area_key
        -> scanned count) is accepted for the caller's sake; the totals are
        counted live on every check, so nothing is stored."""
        self.reset_quest_plateau()

    def reset_quest_plateau(self):
        """Clear plateau tracking so the next scan cycle starts fresh."""
        for state in self._quest_plateau.values():
            state["prev_count"] = -1
            state["flat_streak"] = 0
        self._plateau_reset_at = time.time()
