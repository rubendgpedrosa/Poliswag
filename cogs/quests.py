import discord, requests, os
from discord.ext import commands

class Quests(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.SCAN_QUESTS_ALL_URL = os.environ.get("SCAN_QUESTS_ALL_URL")
        self.QUESTS_IMAGE_FILE = os.environ.get("QUESTS_IMAGE_FILE")
        self.QUESTS_IMAGE_MAP_FILE = os.environ.get("QUESTS_IMAGE_MAP_FILE")
        self.QUESTS_COMPOSITE_IMAGE_FILE = os.environ.get("QUESTS_COMPOSITE_IMAGE_FILE")

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
        if ctx.guild is not None:
            await ctx.message.delete()

        if len(ctx.message.content.split(" ")) < 2:
            await ctx.send("Argumentos inválidos! Usa: !questleiria/!questmarinha POKÉSTOP/QUEST/RECOMPENSA")
            return

        search = ctx.message.content.replace("!questleiria", "").replace("!questmarinha", "").strip()
        isLeiria = ctx.invoked_with == "questleiria"
        user = ctx.author

        foundQuests = self.poliswag.quest_search.find_quest_by_search_keyword(search.lower(), isLeiria)
        if not foundQuests:
            await ctx.send(f"{user.mention}, não foram encontradas quests {'em Leiria' if isLeiria else 'na Marinha'} para '{search}'!")
            return

        self.poliswag.image_generator.generate_image_from_quest_data(foundQuests, isLeiria)
        self.poliswag.image_generator.generate_map_image_from_quest_data(foundQuests)
        combinedImagePath = self.poliswag.image_generator.combine_images()

        if combinedImagePath:  # Check if image combination was successful
            try:
                with open(self.QUESTS_COMPOSITE_IMAGE_FILE, "rb") as imageFile:
                    await ctx.send(f"{user.mention}, aqui estão os resultados para '{search}':", file=discord.File(imageFile, filename=self.QUESTS_COMPOSITE_IMAGE_FILE))
            except discord.HTTPException as e:
                print(f"Error sending image to Discord: {e}")
                await ctx.send(f"{user.mention}, ocorreu um erro ao enviar a imagem. Tente novamente mais tarde.")
            except FileNotFoundError as e: # Catch file not found
                print(f"Combined image not found: {e}")
                await ctx.send(f"{user.mention}, ocorreu um erro ao processar a imagem. Tente novamente mais tarde.")
            except Exception as e:
                print(f"An unexpected error occurred: {e}")
                await ctx.send(f"{user.mention}, ocorreu um erro inesperado. Tente novamente mais tarde.")
        else:
            await ctx.send(f"{user.mention}, ocorreu um erro ao gerar a imagem combinada. Tente novamente mais tarde.")

async def setup(poliswag):
    await poliswag.add_cog(Quests(poliswag))