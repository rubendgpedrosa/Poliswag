import io
import time

import discord
from modules.http_client import fetch_data
from modules.logging_mixin import LoggingMixin

DISABLED_STATUSES = [
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


def _device_is_connected(device):
    # RotomNG exposes connectivity as `is_connected`; the legacy Node Rotom
    # used `isAlive`. Accept either so this survives the cutover + rollback.
    return bool(device.get("is_connected", device.get("isAlive", False)))


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


class AccountMonitor(LoggingMixin):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        # Cached status-message reference — avoids re-scanning up to 50
        # channel messages every 60s tick just to re-find the bot's own
        # message to edit. Cleared if the message turns out to be gone, so
        # the next tick rediscovers (or resends) it.
        self._accounts_message = None

    async def get_account_stats(self):
        account_stats = await fetch_data("account_status", log_fn=self._log)
        if not account_stats:
            return {"in_use": 0, "good": 0, "cooldown": 0, "disabled": 0}

        disabled = sum(account_stats.get(s, 0) for s in DISABLED_STATUSES)
        return {
            "in_use": account_stats.get("in_use", 0),
            "good": account_stats.get("good", 0),
            "cooldown": account_stats.get("cooldown", 0),
            "disabled": disabled,
        }

    async def get_device_snapshot(self):
        """Connectivity flag + worker counters from a single Rotom fetch.

        The status card needs both on every 60s tick; fetching once here
        keeps that one HTTP call rather than two.
        """
        device_status = await fetch_data("device_status", log_fn=self._log)
        devices = (device_status or {}).get("devices") or []
        return {
            "connected": any(_device_is_connected(d) for d in devices),
            "workers": _worker_counts(devices),
        }

    async def is_device_connected(self):
        return (await self.get_device_snapshot())["connected"]

    @staticmethod
    def _build_status_body():
        """Message body carrying the card's last-update time.

        Rendered as Discord timestamp markup rather than a preformatted
        string so every reader sees it in their own timezone, and the
        relative half keeps counting up between ticks -- that is what
        makes a stalled card obvious, since an image that stops being
        refreshed otherwise looks identical to a fresh one.
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

            account_data = await self.get_account_stats()
            device_snapshot = await self.get_device_snapshot()
            device_status = device_snapshot["connected"]
            area_performance = (
                await self.poliswag.scanner_status.get_iv_verification_by_area()
            )
            image_bytes = (
                await self.poliswag.image_generator.generate_image_from_account_stats(
                    account_data,
                    device_status,
                    area_performance,
                    workers=device_snapshot["workers"],
                )
            )

            if not image_bytes:
                self._log("Error generating account image")
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
                    return
                except discord.NotFound:
                    # Message was deleted out from under us — fall through to
                    # resend and re-cache below.
                    self._accounts_message = None

            self._accounts_message = await self.poliswag.ACCOUNTS_CHANNEL.send(
                content=content, file=discord_file
            )

        except Exception as e:
            self._log(f"Error in update_channel_accounts_stats: {e}")
