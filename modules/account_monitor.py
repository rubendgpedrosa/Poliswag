import io
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

    async def is_device_connected(self):
        device_status = await fetch_data("device_status", log_fn=self._log)
        if not device_status or "devices" not in device_status:
            return False
        # RotomNG exposes connectivity as `is_connected`; the legacy Node Rotom
        # used `isAlive`. Accept either so this survives the cutover + rollback.
        return any(
            device.get("is_connected", device.get("isAlive", False))
            for device in device_status["devices"]
        )

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
            device_status = await self.is_device_connected()
            image_bytes = (
                await self.poliswag.image_generator.generate_image_from_account_stats(
                    account_data, device_status
                )
            )

            if not image_bytes:
                self._log("Error generating account image")
                return

            discord_file = discord.File(
                io.BytesIO(image_bytes), filename="account_status_report.png"
            )

            if self._accounts_message:
                try:
                    # embed=None + attachments=[...] fully replaces whatever
                    # the cached message had before -- either a stale image
                    # attachment, or the plain embed from an earlier design.
                    await self._accounts_message.edit(
                        content=None, embed=None, attachments=[discord_file]
                    )
                    return
                except discord.NotFound:
                    # Message was deleted out from under us — fall through to
                    # resend and re-cache below.
                    self._accounts_message = None

            self._accounts_message = await self.poliswag.ACCOUNTS_CHANNEL.send(
                file=discord_file
            )

        except Exception as e:
            self._log(f"Error in update_channel_accounts_stats: {e}")
