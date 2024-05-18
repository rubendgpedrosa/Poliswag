import discord
from discord.ui import Button, View

class RoleManager:
    async def prepare_view_roles_location(self, channel):
        buttonLeiria = Button(label="POKÉMON LEIRIA", style=discord.ButtonStyle.secondary, custom_id="AlertasLeiria", row=0)
        buttonMarinha = Button(label="POKÉMON MARINHA", style=discord.ButtonStyle.secondary, custom_id="AlertasMarinha", row=0)
        buttonRaidsLeiria = Button(label="@LEIRIA", style=discord.ButtonStyle.secondary, custom_id="Leiria", row=2)
        buttonRaidsMarinha = Button(label="@MARINHA", style=discord.ButtonStyle.secondary, custom_id="Marinha", row=2)
        buttonRemote = Button(label="@REMOTE", style=discord.ButtonStyle.secondary, custom_id="Remote", row=2)
        buttonRaids = Button(label="ALERTAS RAIDS", style=discord.ButtonStyle.secondary, custom_id="AlertasRaids", row=1)
        buttonPvP = Button(label="ALERTAS PvP", style=discord.ButtonStyle.secondary, custom_id="AlertasPvP", row=1)

        await self.add_button_event(buttonLeiria)
        await self.add_button_event(buttonMarinha)
        await self.add_button_event(buttonRaidsLeiria)
        await self.add_button_event(buttonRaidsMarinha)
        await self.add_button_event(buttonRemote)
        await self.add_button_event(buttonPvP)
        await self.add_button_event(buttonRaids)

        view = View()
        view.add_item(buttonLeiria)
        view.add_item(buttonMarinha)
        view.add_item(buttonRaidsLeiria)
        view.add_item(buttonRaidsMarinha)
        view.add_item(buttonRemote)
        view.add_item(buttonPvP)
        view.add_item(buttonRaids)

        embed = discord.Embed(title="NOTIFICAÇÕES DE POKÉMON/RAIDS", description="Para desativar ou ativar as notificações, basta clicar no botão correspondente.\n", color=0x7b83b4)
        embed.add_field(name="POKÉMON LEIRIA/MARINHA", value="Receber notificações do aparecimento dos pokémon no centro de Leiria e/ou Marinha Grande de diferentes IVs;", inline=False)
        embed.add_field(name="ALERTAS RAIDS/PvP", value="Receber notificações de Raids e/ou de pokémon ideais para PvP;", inline=False)
        embed.add_field(name="@LEIRIA/@MARINHA/@REMOTE", value="Receber menções quando identificam o role Leiria, Marinha ou remote;", inline=False)
        await channel.send(embed=embed, view=view)

    async def prepare_view_roles_teams(self, channel):
        buttonInstinct = Button(label="INSTINCT", style=discord.ButtonStyle.secondary, custom_id="Instinct", emoji="<:instinct:596737766038175775>")
        buttonMystic = Button(label="MYSTIC", style=discord.ButtonStyle.secondary, custom_id="Mystic", emoji="<:mystic:596737698056634389>")
        buttonValor = Button(label="VALOR", style=discord.ButtonStyle.secondary, custom_id="Valor", emoji="<:valor:596737734920503314>")

        await self.add_button_event(buttonInstinct)
        await self.add_button_event(buttonMystic)
        await self.add_button_event(buttonValor)

        view = View()
        view.add_item(buttonInstinct)
        view.add_item(buttonMystic)
        view.add_item(buttonValor)

        embed = discord.Embed(title="SELEÇÃO DE EQUIPA", description="Mais uma vez, sejam bem vindos ao discord **PoGoLeiria**, para começares e desbloqueares os restantes canais, pressiona no botão correspondente à tua equipa no jogo.\nPara as **notificações dos spawns de Pokémon/Raids**, podem-nas ativar em <#985883269193146408>", color=0x7b83b4)
        await channel.send(embed=embed, view=view)

    async def response_user_role_selection(self, interaction):
        await self.toggle_role(interaction.data["custom_id"], interaction.user)
        await interaction.response.defer()

    async def add_button_event(self, button):
        button.callback = self.response_user_role_selection

    async def restart_response_user_role_selection(self, interaction):
        await self.response_user_role_selection(interaction)

    async def toggle_role(self, role, user):
        roles_list = ["Instinct", "Mystic", "Valor"]
        notif_list = ["AlertasLeiria", "AlertasMarinha", "AlertasRaids", "AlertasPvP", "Remote"]
        roleToToggle = discord.utils.get(user.guild.roles, name=role)
        if roleToToggle:
            if roleToToggle in user.roles and role not in roles_list:
                await user.remove_roles(roleToToggle, atomic=True)
            elif roleToToggle not in user.roles:
                if len(user.roles) <= 1:
                    for notifRolesToAdd in notif_list:
                        roleListNotif = discord.utils.get(user.guild.roles, name=notifRolesToAdd)
                        await user.add_roles(roleListNotif, atomic=True)
                await self.remove_team_roles(role, user)
                await user.add_roles(roleToToggle, atomic=True)

    def build_response_message(self, role):
        if role.lower() == "alertasleiria" or role.lower() == "alertasmarinha" or role.lower() == "alertaspvp" or role.lower() == "alertasraids" or role.lower() == "remote":
            return "Notificações de " + role.title()
        elif role.lower() == "mystic" or role.lower() == "valor" or role.lower() == "instinct":
            return "Equipa atribuida"
            
    async def remove_team_roles(self, role, user):
        roles_list = ["Instinct", "Mystic", "Valor"]
        if role not in roles_list:  
            return
        roles_list.remove(role)

        for role_list in roles_list:
            roleToRemove = discord.utils.get(user.guild.roles, name=role_list)
            if roleToRemove in user.roles:
                await user.remove_roles(roleToRemove, atomic=True)

    async def build_rules_message(self, message):
        embed = discord.Embed(
            title="REGRAS DO SERVIDOR",
            description="Qualquer questão relacionada com a gestão do servidor, entrem em contacto com Moderadores ou Administrador.",
            color=0x7b83b4
        )
        embed.add_field(name="Nada de conteúdo pornográfico, odioso, prejudicial e/ou ofensioso", value="Todo conteúdo que encaixe nestes moldes, sofrera de tolerância zero e será alvo de moderação, sendo avatares também abrangidos por estas regras;", inline=False)
        embed.add_field(name="Proibido spam e cross channel spam", value="Qualquer canal de discussão pública não deve ser usado para qualquer tipo de publicidade e/ou spam/cross channel spam;", inline=False)
        embed.add_field(name="Proibida conversa sobre troca e venda de contas", value="De modo a tornar o jogo mais justo, toda e qualquer conversa relacionada com a compra/venda de contas e/ou troca/partilha de contas, é proibida;", inline=False)
        embed.add_field(name="Botting e GPS Spoofing;", value="Nada de discussão ou conversas relacionadas com ações de botting ou GPS spoofing que prejudicam outros jogadores;", inline=False)
        embed.add_field(name="A administração poderá moderar à sua discrição", value="Sendo impossísvel prever todas as situações, a equipa de administração poderá moderar qualquer assunto que considerem impróprios. Como tal, bom senso nas interações com os restantes membros da comunidade.", inline=False)
        await message.channel.send(
            content='Bem vindos ao Discord _Pokemon Go Leiria_, um projecto disponibilizado por **PoGoLeiria**.\nPoGoLeiria procura dinamizar a comunidade local, oferecendo notificações **GRATUITAMENTE** de _RAIDS_ e _SPAWNS POKÉMON_, em tempo real, do centro de **Leiria** e da **Marinha Grande**.\nO scanner de pokémon pode ser encontrado em: https://pogoleiria.pt\n\n_ _\n',
            embed=embed
        )
        await self.prepare_view_roles_teams(message.channel)

    async def strip_user_roles(self, user):
        if isinstance(user, discord.Member):
            for role in user.roles:
                try:
                    await user.remove_roles(role)
                except discord.NotFound:
                    print(f"Role {role} not found when removing roles from {user}")
                except discord.Forbidden:
                    print(f"Bot doesn't have permission to manage roles when removing roles from {user}")
        else:
            print(f"User {user} is not a member of the server.")
