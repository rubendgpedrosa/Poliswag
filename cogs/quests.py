import discord, requests, os
from discord.ext import commands


class Quests(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.SCAN_QUESTS_ALL_URL = os.environ.get("SCAN_QUESTS_ALL_URL")

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    @commands.command(name="scan")
    async def rescancmd(self, ctx):
        request = requests.get(self.SCAN_QUESTS_ALL_URL)
        if request.status_code == 200:
            await ctx.send("Scan de quests iniciado!")
            self.poliswag.scanner_manager.update_quest_scanning_state(0)
        else:
            await ctx.send("Erro ao iniciar o scan de quests!")

    @commands.command(name="questleiria", aliases=["questmarinha"])
    async def questcmd(self, ctx):
        search = (
            ctx.message.content.replace("!questleiria", "")
            .replace("!questmarinha", "")
            .strip()
        )
        isLeiria = ctx.invoked_with == "questleiria"
        user = ctx.author

        foundQuests = self.poliswag.quest_search.find_quest_by_search_keyword(
            search.lower(), isLeiria
        )
        if not foundQuests:
            await ctx.send(
                f"{user.mention}, não foram encontradas quests {'em Leiria' if isLeiria else 'na Marinha'} para '{search}'!"
            )
            return

        # Generate images into memory
        self.poliswag.image_generator.generate_image_from_quest_data(
            foundQuests, isLeiria
        )
        self.poliswag.image_generator.generate_map_image_from_quest_data(foundQuests)
        combinedBuffer = self.poliswag.image_generator.combine_images()

        if combinedBuffer:
            try:
                await ctx.send(
                    f"{user.mention}, aqui estão os resultados para '{search}':",
                    file=discord.File(combinedBuffer, filename="quest_results.png"),
                )
            except Exception as e:
                print(f"Erro ao enviar imagem: {e}")
                await ctx.send(f"{user.mention}, um erro occorreu ao enviar a imagem.")
        else:
            await ctx.send(
                f"{user.mention}, ocorreu um erro ao gerar a imagem combinada. Tente novamente mais tarde."
            )


async def setup(poliswag):
    await poliswag.add_cog(Quests(poliswag))
