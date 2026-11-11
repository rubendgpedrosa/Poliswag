import io
import json
import time

import discord

from modules.config import Config
from modules.http_client import fetch_data
from modules.logging_mixin import LoggingMixin
from modules.utility import FailureStreak, describe_error

# Dragonite's /accounts/stats buckets. These partition `total`: every account
# is in exactly one of them. `missing_token` is deliberately absent -- it is an
# attribute that overlaps the buckets (an invalid account is also missing a
# token, and so is a fresh one waiting for its first login), so summing it
# double-counts.
OUT_STATUSES = (
    "banned",
    "auth_banned",
    "suspended",
    "invalid",
    "warned",
    "disabled",
    "provider_disabled",
)
PARTITION_STATUSES = ("in_use", "good", "cooldown", "zero_last_released") + (
    OUT_STATUSES
)

# Pools shown on the card: (key, title, account endpoint, device endpoint).
# The levelling pool is optional -- unset endpoints hide its section.
POOLS = (
    ("scanning", "Scanning", "account_status", "device_status"),
    ("levelling", "Levelling", "level_account_status", "level_device_status"),
)

# The card's account and device numbers are read every minute, but the image
# is rendered and uploaded only when one of them changed: until 2026-11-11 it
# was redrawn and re-uploaded 1,440 times a day whatever it showed. The area
# footer (spawns per hour) moves a little every minute without saying
# anything new, so on its own it redraws the card only this often.
CARD_REFRESH_SECONDS = 300

# A failed update (a connection reset, a Discord 503/504, a render hiccup) is
# tried again on the next minute, and the message's "Last updated" relative
# time already shows when the card is stale. A failure is a WARNING until
# this many updates in a row have failed (about ten minutes); then it is one
# ERROR, which reaches the owner's daily error digest.
ACCOUNT_CARD_ERROR_AFTER = 10


def _device_is_connected(device):
    # RotomNG exposes connectivity as `is_connected`; the legacy Node Rotom
    # used `isAlive`. Accept either so this survives the cutover + rollback.
    return bool(device.get("is_connected", device.get("isAlive", False)))


def _device_name(device):
    """Short device name: RotomNG `id`, else `origin` without its prefix."""
    name = device.get("id") or device.get("origin") or device.get("deviceId") or "?"
    return str(name).rsplit("/", 1)[-1]


def _worker_counts(devices):
    """Total/in-use worker counts summed over the *connected* devices.

    Only RotomNG reports per-device worker figures (`worker_count` /
    `worker_in_use_count`); the legacy Node Rotom payload carries no
    equivalent, so it sums to zero and the card hides the figure rather
    than rendering a misleading "0 workers". Disconnected devices are
    skipped -- Rotom keeps reporting their last-known worker_count after
    they drop, which would otherwise show a full worker pool for a device
    that is offline.
    """
    total = 0
    in_use = 0
    for device in devices:
        if not _device_is_connected(device):
            continue
        total += int(device.get("worker_count") or 0)
        in_use += int(device.get("worker_in_use_count") or 0)
    return {"total": total, "in_use": in_use}


def summarize_accounts(stats):
    """Turn a Dragonite /accounts/stats payload into the card's breakdown.

    in_use + ready + cooldown + out == total, where ready = good + never used
    (`zero_last_released`: never handed out yet, so usable) and out = every
    unusable bucket. `partition_sum` is reported next to Dragonite's own
    `total` so a caller can notice a bucket this code does not know about.
    """
    stats = stats or {}

    def n(key):
        return int(stats.get(key) or 0)

    out_detail = {s: n(s) for s in OUT_STATUSES if n(s)}
    summary = {
        "in_use": n("in_use"),
        "good": n("good"),
        "never_used": n("zero_last_released"),
        "cooldown": n("cooldown"),
        "out": sum(out_detail.values()),
        "out_detail": out_detail,
        "missing_token": n("missing_token"),
        "partition_sum": sum(n(s) for s in PARTITION_STATUSES),
    }
    summary["ready"] = summary["good"] + summary["never_used"]
    summary["total"] = n("total") or summary["partition_sum"]
    return summary


class AccountMonitor(LoggingMixin):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        # Cached status-message reference — avoids re-scanning up to 50
        # channel messages every 60s tick just to re-find the bot's own
        # message to edit. Cleared if the message turns out to be gone, so
        # the next tick rediscovers (or resends) it.
        self._accounts_message = None
        # Last (partition_sum, total) logged per pool, so a mismatch is
        # logged when it appears or changes, not on every 60s tick.
        self._partition_warned = {}
        self._card_failures = FailureStreak(ACCOUNT_CARD_ERROR_AFTER)
        # What the card in the channel shows and when it was drawn
        # (monotonic); None until this process has drawn it once, so a
        # restart always redraws.
        self._card_numbers = None
        self._card_drawn_at = 0.0

    async def get_account_stats(self):
        """Scanning-pool account counts for the status embeds and alerts.

        `good`/`in_use`/`cooldown` are Dragonite's own buckets; `disabled` is
        the accounts that cannot be used at all (banned, invalid, ...). The
        fuller breakdown the card draws comes from get_pool_stats().
        """
        account_stats = await fetch_data("account_status", log_fn=self._log)
        if not account_stats:
            return {"in_use": 0, "good": 0, "cooldown": 0, "disabled": 0}
        summary = summarize_accounts(account_stats)
        return {
            "in_use": summary["in_use"],
            "good": summary["good"],
            "cooldown": summary["cooldown"],
            "disabled": summary["out"],
            "ready": summary["ready"],
            "never_used": summary["never_used"],
            "total": summary["total"],
        }

    def _check_partition(self, key, summary):
        pair = (summary["partition_sum"], summary["total"])
        if pair[0] == pair[1]:
            self._partition_warned.pop(key, None)
            return
        if self._partition_warned.get(key) != pair:
            self._partition_warned[key] = pair
            self._log(
                f"{key} account buckets sum to {pair[0]} but Dragonite reports "
                f"{pair[1]} in total; the card uses Dragonite's total",
                "WARNING",
            )

    async def _fetch_device(self, device_key):
        """Connected flag, device names and worker counts; None if Rotom failed."""
        device_status = await fetch_data(device_key, log_fn=self._log)
        if device_status is None:
            return None
        devices = device_status.get("devices") or []
        connected = [d for d in devices if _device_is_connected(d)]
        return {
            "connected": bool(connected),
            "names": [_device_name(d) for d in connected],
            "workers": _worker_counts(devices),
        }

    async def get_pool_stats(self):
        """One entry per configured pool, in card order.

        A pool whose account endpoint is not configured is left out. An
        endpoint that is configured but fails yields `accounts: None` (or
        `device: None`) so the card can say "unavailable" for that pool
        without losing the other one.
        """
        pools = []
        for key, title, account_key, device_key in POOLS:
            if key != "scanning" and not Config.ENDPOINTS.get(account_key):
                continue
            stats = await fetch_data(account_key, log_fn=self._log)
            accounts = None
            if stats:
                accounts = summarize_accounts(stats)
                self._check_partition(key, accounts)
            device = None
            if key == "scanning" or Config.ENDPOINTS.get(device_key):
                device = await self._fetch_device(device_key)
            pools.append(
                {"key": key, "title": title, "accounts": accounts, "device": device}
            )
        return pools

    async def get_device_snapshot(self):
        """Connectivity flag + worker counters from a single Rotom fetch."""
        device = await self._fetch_device("device_status")
        if device is None:
            return {"connected": False, "workers": {"total": 0, "in_use": 0}}
        return {"connected": device["connected"], "workers": device["workers"]}

    async def is_device_connected(self):
        return (await self.get_device_snapshot())["connected"]

    async def connected_worker_count(self) -> int | None:
        """Phone (PoliSnatch) workers registered in rotom-ng, or None if it didn't answer.

        An offline phone counts as 0. None is kept apart from 0 because the
        recovery ladder treats "rotom-ng is down" and "no workers" differently.
        """
        device_status = await fetch_data("device_status", log_fn=self._log)
        if device_status is None:
            return None
        return _worker_counts(device_status.get("devices") or [])["total"]

    async def build_account_card(self, pools=None):
        """PNG bytes of the account card (both pools + area footer), or None.

        `pools` is get_pool_stats()'s answer when the caller already has it.
        """
        if pools is None:
            pools = await self.get_pool_stats()
        area_performance = (
            await self.poliswag.scanner_status.get_iv_verification_by_area()
        )
        return await self.poliswag.image_generator.generate_image_from_account_stats(
            pools, area_performance
        )

    @staticmethod
    def _build_status_body():
        """Message body carrying the card's last-update time.

        Rendered as Discord timestamp markup rather than a preformatted
        string so every reader sees it in their own timezone, and the
        relative half keeps counting up between updates -- that is what
        makes a stalled card obvious, since an image that stops being
        refreshed otherwise looks identical to a fresh one. The card is
        redrawn at least every CARD_REFRESH_SECONDS, so a time much older
        than that means it has stalled.
        """
        updated_at = int(time.time())
        return f"Last updated: <t:{updated_at}:f> (<t:{updated_at}:R>)"

    async def update_channel_accounts_stats(self):
        if self.poliswag.ACCOUNTS_CHANNEL is None:
            return
        try:
            if self._accounts_message is None:
                async for message in self.poliswag.ACCOUNTS_CHANNEL.history(limit=50):
                    if message.author == self.poliswag.user:
                        self._accounts_message = message
                        break

            pools = await self.get_pool_stats()
            numbers = json.dumps(pools, sort_keys=True, default=str)
            now = time.monotonic()
            if (
                self._accounts_message is not None
                and numbers == self._card_numbers
                and now - self._card_drawn_at < CARD_REFRESH_SECONDS
            ):
                return

            image_bytes = await self.build_account_card(pools)

            if not image_bytes:
                # The renderer has already logged why; only count it here.
                self._card_failed(None)
                return

            discord_file = discord.File(
                io.BytesIO(image_bytes), filename="account_status_report.png"
            )

            content = self._build_status_body()

            if self._accounts_message:
                try:
                    # embed=None + attachments=[...] fully replaces whatever
                    # the cached message had before -- either a stale image
                    # attachment, or the plain embed from an earlier design.
                    # content is passed explicitly on every edit so the
                    # timestamp actually advances -- .edit() leaves anything
                    # not passed exactly as it was.
                    await self._accounts_message.edit(
                        content=content, embed=None, attachments=[discord_file]
                    )
                    self._card_drawn(numbers, now)
                    return
                except discord.NotFound:
                    # Message was deleted out from under us — fall through to
                    # resend and re-cache below.
                    self._accounts_message = None

            self._accounts_message = await self.poliswag.ACCOUNTS_CHANNEL.send(
                content=content, file=discord_file
            )
            self._card_drawn(numbers, now)

        except Exception as e:
            self._card_failed(e)

    def _card_drawn(self, numbers, now):
        """Discord took the card: remember what it shows. A failed update
        never reaches here, so it is tried again on the next minute."""
        self._card_numbers = numbers
        self._card_drawn_at = now
        self._card_failures.reset()

    def _card_failed(self, error):
        """WARNING per failed update (none when the renderer already logged
        it); one ERROR when ACCOUNT_CARD_ERROR_AFTER updates in a row failed."""
        if self._card_failures.fail():
            detail = f": {describe_error(error)}" if error is not None else ""
            self._log(
                f"Account card update failed {ACCOUNT_CARD_ERROR_AFTER} times "
                f"in a row{detail}",
                "ERROR",
            )
        elif error is not None:
            self._log(
                f"Error in update_channel_accounts_stats: {describe_error(error)}",
                "WARNING",
            )
