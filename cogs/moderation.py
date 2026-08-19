import discord
from discord.ext import commands

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _is_image_attachment(attachment):
    if attachment.content_type and attachment.content_type.startswith("image/"):
        return True
    return attachment.filename.lower().endswith(_IMAGE_EXTENSIONS)


class Moderation(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    @commands.Cog.listener()
    async def on_interaction(self, interaction):
        if not interaction.data or "custom_id" not in interaction.data:
            return

        custom_id = interaction.data["custom_id"]
        if custom_id.startswith("Alertas") or custom_id in [
            "Leiria",
            "Marinha",
            "Remote",
            "Mystic",
            "Valor",
            "Instinct",
        ]:
            await self.poliswag.role_manager.response_user_role_selection(interaction)

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        mod_channel = self.poliswag.MOD_CHANNEL
        quest_channel = self.poliswag.QUEST_CHANNEL
        if (
            mod_channel is None
            or quest_channel is None
            or message.channel.id in [mod_channel.id, quest_channel.id]
            or str(message.author.id) in self.poliswag.ADMIN_USERS_IDS
            or message.author == self.poliswag.user
        ):
            return

        embed = discord.Embed(
            title=f"[{message.channel}] Mensagem removida", color=0x7B83B4
        )
        embed.add_field(
            name=str(message.author),
            value=message.content or "*(sem texto)*",
            inline=False,
        )

        image_attachment = next(
            (a for a in message.attachments if _is_image_attachment(a)), None
        )
        if image_attachment:
            embed.set_image(url=image_attachment.url)

        other_attachments = [
            a for a in message.attachments if a is not image_attachment
        ]
        if other_attachments:
            embed.add_field(
                name="Anexos",
                value="\n".join(f"[{a.filename}]({a.url})" for a in other_attachments),
                inline=False,
            )

        await self.poliswag.utility.send_embed_to_channel(mod_channel, embed)


async def setup(poliswag):
    await poliswag.add_cog(Moderation(poliswag))
