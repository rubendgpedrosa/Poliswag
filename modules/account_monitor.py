import io
import datetime
import discord
from modules.http_client import fetch_data


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


class AccountMonitor:
    def __init__(self, poliswag):
        self.poliswag = poliswag

    def _log(self, msg, level="ERROR"):
        self.poliswag.utility.log_to_file(msg, level)

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
        return any(device.get("isAlive", False) for device in device_status["devices"])

    async def update_channel_accounts_stats(self):
        if self.poliswag.ACCOUNTS_CHANNEL is None:
            return
        try:
            existing_message = None
            async for message in self.poliswag.ACCOUNTS_CHANNEL.history(limit=None):
                if message.author == self.poliswag.user and not existing_message:
                    existing_message = message
                else:
                    await message.delete()

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

            timestamp_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            discord_file = discord.File(
                io.BytesIO(image_bytes), filename="account_status_report.png"
            )
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
            self._log(f"Error in update_channel_accounts_stats: {e}")
