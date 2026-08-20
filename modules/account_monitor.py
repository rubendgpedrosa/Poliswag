import datetime
import discord
from modules.embeds import status_embed
from modules.http_client import fetch_data
from modules.logging_mixin import LoggingMixin

# (color, emoji) per severity level for the account-pool status embed.
_STATUS_LEVELS = {
    "ok": (discord.Color.green(), "🟢"),
    "warn": (discord.Color.gold(), "🟡"),
    "crit": (discord.Color.red(), "🔴"),
}

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

    def build_status_embed(self, account_data, device_status):
        """Build the account-pool status embed, used both by the
        auto-updating ACCOUNTS_CHANNEL board and the on-demand !accounts
        command.

        A plain embed rather than a rendered image: the auto-updating board
        refreshes every 60s forever, and the payload is just 3 counts + a
        boolean -- an embed's color/emoji/bold-number vocabulary already
        gives an at-a-glance health signal, is automatically theme-correct
        (dark/light), and needs no imgkit/wkhtmltoimage render step.

        Deliberately minimal: no description sentence and no bar chart --
        the title's color/emoji plus the four field values already say
        everything at a glance.
        """
        good = account_data.get("good", 0)
        cooldown = account_data.get("cooldown", 0)
        disabled = account_data.get("disabled", 0)
        total = max(good + cooldown + disabled, 1)

        # Some accounts sitting in cooldown/disabled at any given moment is
        # normal pool churn, not itself a problem -- only the device being
        # down or the *available* share running thin actually warrants a
        # warning/critical color.
        if not device_status or good == 0:
            level = "crit"
        elif good / total < 0.3:
            level = "warn"
        else:
            level = "ok"

        color, dot = _STATUS_LEVELS[level]

        embed = status_embed(f"{dot} Pool de Contas", color=color)
        embed.add_field(name="Disponíveis", value=f"**{good}**", inline=True)
        embed.add_field(name="Cooldown", value=f"**{cooldown}**", inline=True)
        embed.add_field(name="Desativadas", value=f"**{disabled}**", inline=True)
        embed.add_field(
            name="Dispositivo",
            value="🟢 Conectado" if device_status else "🔴 Desconectado",
            inline=False,
        )
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        return embed

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
            embed = self.build_status_embed(account_data, device_status)

            if self._accounts_message:
                try:
                    # attachments=[] is required here, not just cosmetic: the
                    # cached message may predate this embed-only design and
                    # still carry the old rendered-image attachment, which
                    # .edit() otherwise keeps by default alongside the embed.
                    await self._accounts_message.edit(
                        content=None, embed=embed, attachments=[]
                    )
                    return
                except discord.NotFound:
                    # Message was deleted out from under us — fall through to
                    # resend and re-cache below.
                    self._accounts_message = None

            self._accounts_message = await self.poliswag.ACCOUNTS_CHANNEL.send(
                embed=embed
            )

        except Exception as e:
            self._log(f"Error in update_channel_accounts_stats: {e}")
