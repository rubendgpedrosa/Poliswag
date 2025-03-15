#!/usr/bin/python\
import discord, os, traceback, datetime
from dotenv import load_dotenv
from discord.ext import commands, tasks

from modules.role_manager import RoleManager
from modules.scanner_status import ScannerStatus
from modules.scanner_manager import ScannerManager
from modules.utility import Utility
from modules.database_connector import DatabaseConnector
from modules.image_generator import ImageGenerator
from modules.quest_search import QuestSearch
from modules.event_manager import EventManager


class Poliswag(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        intents.messages = True
        super().__init__(command_prefix="!", intents=intents)

        load_dotenv()  # Load environment variables from .env file

        """ IMPORTED CLASSES """
        self.db = DatabaseConnector()
        self.role_manager = RoleManager()
        self.utility = Utility(
            self
        )  # Utility methods like building embeds and cURL requests
        self.scanner_status = ScannerStatus(self)
        self.scanner_manager = ScannerManager(self)
        self.image_generator = ImageGenerator(self)
        self.quest_search = QuestSearch(self)
        self.event_manager = EventManager(self)
        """ ! IMPORTED CLASSSES ! """

        """ CHANNEL'S INITIAL SETUP """
        self.QUEST_CHANNEL = None
        self.CONVIVIO_CHANNEL = None
        self.MOD_CHANNEL = None
        self.ACCOUNTS_CHANNEL = None
        """ ! CHANNEL'S INITIAL SETUP ! """

        """ USER IDS """
        self.ADMIN_USERS_IDS = os.environ.get("ADMIN_USERS_IDS").split(",")
        """ ! USER IDS ! """

        self.quest_scanning_message = None

    async def on_ready(self):
        await self.get_channels()
        await self.scheduled_tasks.start()

    async def setup_hook(self):
        await self.load_extension("cogs.quests")
        await self.load_extension("cogs.accounts")
        await self.load_extension("cogs.tracker")
        await self.tree.sync()

    async def get_channels(self):
        self.QUEST_CHANNEL = await self.fetch_channel(
            int(os.environ.get("QUEST_CHANNEL_ID"))
        )
        self.CONVIVIO_CHANNEL = await self.fetch_channel(
            int(os.environ.get("CONVIVIO_CHANNEL_ID"))
        )
        self.MOD_CHANNEL = await self.fetch_channel(
            int(os.environ.get("MOD_CHANNEL_ID"))
        )
        self.ACCOUNTS_CHANNEL = await self.fetch_channel(
            int(os.environ.get("ACCOUNTS_CHANNEL_ID"))
        )

    @tasks.loop(seconds=60)
    async def scheduled_tasks(self):
        try:
            """UPDATE FILES DATA"""
            self.quest_search.load_translation_data()
            self.quest_search.load_masterfile_data()
            self.quest_search.generate_pokemon_item_name_map()
            await self.event_manager.fetch_events()
            """ ! UPDATE FILES DATA ! """

            """ NEW FORCED VERSIONS """
            new_version = await self.utility.get_new_pokemongo_version()
            if new_version is not None:
                await self.CONVIVIO_CHANNEL.send(
                    embed=self.utility.build_embed_object_title_description(
                        "PAAAAAAAAAUUUUUUUUUU!!! FORCE UPDATE!",
                        f"Nova versão: {new_version}",
                    )
                )
            """ ! NEW FORCED VERSIONS ! """

            """ DETECT DAY CHANGE & CHECK QUEST SCANNING COMPLETION """
            day_changed = self.scanner_manager.is_day_change()
            if day_changed:
                self.quest_scanning_message = await self.QUEST_CHANNEL.send(
                    embed=self.utility.build_embed_object_title_description(
                        "SCAN DE QUESTS INICIADO!",
                        "A recolher quests em Leiria e Marinha Grande...",
                    )
                )
            else:
                quest_completed = await self.scanner_status.is_quest_scanning_complete()

                if quest_completed is not None:
                    if self.quest_scanning_message is None:
                        self.quest_scanning_message = (
                            await self.utility.find_quest_scanning_message(
                                self.QUEST_CHANNEL
                            )
                        )

                    if (
                        quest_completed["leiriaCompleted"]
                        and quest_completed["marinhaCompleted"]
                    ):
                        self.scanner_manager.update_quest_scanning_state()

                        embed = self.utility.build_embed_object_title_description(
                            "✅ SCAN DE QUESTS CONCLUÍDO!",
                            (
                                "**Todas as quests diárias foram recolhidas com sucesso!**\n\n"
                                f"**Leiria:** {quest_completed['leiriaScanned']}/{quest_completed['leiriaTotal']} Quests\n"
                                f"**Marinha:** {quest_completed['marinhaScanned']}/{quest_completed['marinhaTotal']} Quests\n\n"
                                "📋 **Como consultar:**\n"
                                "`!questleiria PESQUISA` - Ver quests de Leiria\n"
                                "`!questmarinha PESQUISA` - Ver quests de Marinha\n"
                            ),
                            footer=f"{datetime.datetime.now().strftime('%d/%m/%Y às %H:%M')}",
                        )

                        """ NOTIFY FOLLOWED QUESTS / REWARDS """
                        await self.quest_search.check_tracked(self.CONVIVIO_CHANNEL)
                        """ ! NOTIFY FOLLOWED QUESTS / REWARDS ! """
                    else:
                        embed = self.utility.build_embed_object_title_description(
                            "SCAN DE QUESTS EM PROGRESSO...",
                            f"**Leiria:** {quest_completed['leiriaScanned']}/{quest_completed['leiriaTotal']} Quests ({quest_completed['leiriaPercentage']:.1f}%)\n"
                            + f"**Marinha:** {quest_completed['marinhaScanned']}/{quest_completed['marinhaTotal']} Quests ({quest_completed['marinhaPercentage']:.1f}%)",
                            footer=f"{datetime.datetime.now().strftime('%d/%m/%Y às %H:%M')}",
                        )

                    await self.quest_scanning_message.edit(embed=embed)
            """ ! DETECT DAY CHANGE & CHECK QUEST SCANNING COMPLETION ! """

            """ START / END OF EVENTS """
            # current_active_events = self.event_manager.get_active_events()
            # if current_active_events is not None:
            #    for event in current_active_events:
            #        await self.CONVIVIO_CHANNEL.send(
            #            content=event["content"],
            #            embed=self.utility.build_embed_object_title_description(
            #                event["name"], event["body"], event["footer"]
            #            ),
            #        )
            """ ! START / END OF EVENTS ! """

            """ FAILING WORKERS """
            workers_status = await self.scanner_status.get_workers_with_issues()
            if workers_status:
                await self.scanner_status.rename_voice_channels(
                    workers_status["downDevicesLeiria"],
                    workers_status["downDevicesMarinha"],
                )
            """ ! FAILING WORKERS ! """

            """ SCANNER ACCOUNTS SCHEDULED IMAGE """
            await self.scanner_status.update_channel_accounts_stats()
            """ ! SCANNER ACCOUNTS SCHEDULED IMAGE ! """
        except Exception as e:
            print("CRASH ---", e)
            traceback.print_exc()  # logs broken line
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self.utility.log_to_file(error_msg, "CRASH")

    @commands.Cog.listener()
    async def on_interaction(self, interaction):
        """PRIMARY ROLES ON DISCORD TO ACCESS NOTIFICATIONS"""
        custom_id = interaction.data["custom_id"]

        if custom_id.startswith("Alertas") or custom_id in [
            "Leiria",
            "Marinha",
            "Remote",
            "Mystic",
            "Valor",
            "Instinct",
        ]:
            await self.role_manager.restart_response_user_role_selection(interaction)
        else:
            await self.role_manager.restart_cancel_rescan_callback(interaction)
        """ ! PRIMARY ROLES ON DISCORD TO ACCESS NOTIFICATIONS ! """

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if (
            message.channel.id not in [self.MOD_CHANNEL.id, self.QUEST_CHANNEL.id]
            and str(message.author.id) not in self.ADMIN_USERS_IDS
            and message.author != self.user
        ):
            embed = discord.Embed(
                title=f"[{message.channel}] Mensagem removida", color=0x7B83B4
            )
            embed.add_field(name=message.author, value=message.content, inline=False)
            await self.MOD_CHANNEL.send(embed=embed)


def main():
    poliswag = Poliswag()
    poliswag.run(os.environ.get("DISCORD_API_KEY"))


if __name__ == "__main__":
    main()
