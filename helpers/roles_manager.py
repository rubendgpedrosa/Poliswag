import discord
from discord.ui import Button, View

async def prepare_view_roles_location(channel):
    buttonLeiria = Button(label="LEIRIA", style=discord.ButtonStyle.secondary, custom_id="AlertasLeiria")
    buttonMarinha = Button(label="MARINHA GRANDE", style=discord.ButtonStyle.secondary, custom_id="AlertasMarinha")
    buttonPvP = Button(label="PvP", style=discord.ButtonStyle.secondary, custom_id="AlertasPvP")
    buttonRaids = Button(label="RAIDS", style=discord.ButtonStyle.secondary, custom_id="AlertasRaids")
    buttonRemote = Button(label="REMOTE", style=discord.ButtonStyle.secondary, custom_id="Remote")

    await add_button_event(buttonLeiria)
    await add_button_event(buttonMarinha)
    await add_button_event(buttonPvP)
    await add_button_event(buttonRaids)
    await add_button_event(buttonRemote)

    view = View()
    view.add_item(buttonLeiria)
    view.add_item(buttonMarinha)
    view.add_item(buttonPvP)
    view.add_item(buttonRaids)
    view.add_item(buttonRemote)

    embed = discord.Embed(title="NOTIFICAÇÕES OPCIONAIS", description="Para desativar ou ativar as notificações, basta clicar no botão correspondente.")
    await channel.send(embed=embed, view=view)

async def prepare_view_roles_teams(channel):
    buttonInstinct = Button(label="INSTINCT", style=discord.ButtonStyle.secondary, custom_id="Instinct", emoji="<:instinct:596737766038175775>")
    buttonMystic = Button(label="MYSTIC", style=discord.ButtonStyle.secondary, custom_id="Mystic", emoji="<:mystic:596737698056634389>")
    buttonValor = Button(label="VALOR", style=discord.ButtonStyle.secondary, custom_id="Valor", emoji="<:valor:596737734920503314>")

    await add_button_event(buttonInstinct)
    await add_button_event(buttonMystic)
    await add_button_event(buttonValor)

    view = View()
    view.add_item(buttonInstinct)
    view.add_item(buttonMystic)
    view.add_item(buttonValor)

    embed = discord.Embed(title="SELEÇÃO DE EQUIPA", description="Mais uma vez, sejam bem vindos ao discord **PoGoLeiria**, para comecares e desbloqueares os restantes canais, pressiona no botão correspondente à tua equipa no jogo.", color=0x7b83b4)
    await channel.send(embed=embed, view=view)

async def response_user_role_selection(interaction):
    await toggle_role(interaction.data["custom_id"], interaction.user)
    await interaction.response.defer()

async def add_button_event(button):
    button.callback = response_user_role_selection

async def start_event_listeners(interaction):
    await response_user_role_selection(interaction)

async def toggle_role(role, user):
    roleToToggle = discord.utils.get(user.guild.roles, name=role)
    if roleToToggle:
        if roleToToggle not in user.roles:
            await remove_team_roles(role, user)
            await user.add_roles(roleToToggle, atomic=True)

def build_response_message(role):
    if role.lower() == "alertasleiria" or role.lower() == "alertasmarinha" or role.lower() == "alertaspvp" or role.lower() == "alertasraids" or role.lower() == "remote":
        return "Notificações de " + role.title()
    elif role.lower() == "mystic" or role.lower() == "valor" or role.lower() == "instinct":
        return "Equipa atribuida"
        
async def remove_team_roles(role, user):
    roles_list = ["Instinct", "Mystic", "Valor"]
    if role not in roles_list:  # if the role is not a team role, then we don't need to remove any team roles
        return
    roles_list.remove(role)

    for role_list in roles_list:
        roleToRemove = discord.utils.get(user.guild.roles, name=role_list)
        if roleToRemove in user.roles:
            await user.remove_roles(roleToRemove, atomic=True)

async def build_rules_message(message):
    await message.delete()
    embed = discord.Embed(
        title="REGRAS DO SERVIDOR",
        description="Qualquer questão relacionada com a gestão do servidor, entrem em contacto com os Moderadores ou Administrador.",
        color=0x7b83b4
    )
    embed.add_field(name="Nada de conteúdo pornográfico, odioso, prejudicial e/ou perigoso", value="Todo conteúdo que encaixe nestes moldes, sofrera de tolerância zero e será alvo de moderação, sendo os avatares também abrangidos por isso;", inline=False)
    embed.add_field(name="Respeitar a comunidade que nela se encontra", value="O canal de discussão pública, #coronavivio não deve ser usado para qualquer tipo de publicidade, spam/cross channel spam, mensagens ofensivas e/ou comentários desnecessários;", inline=False)
    embed.add_field(name="Proibida conversa sobre troca e venda de contas", value="De modo a tornar o jogo mais justo, toda e qualquer conversa relacionada com a compra e venda de contas ou troca/partilha de contas é proibida;", inline=False)
    embed.add_field(name="Proibido spam e cross channel spam", value="Qualquer canal de discussão pública não deve ser usado para qualquer tipo de publicidade, spam/cross channel spam, mensagens ofensivas e/ou comentários desnecessários;", inline=False)
    embed.add_field(name="Botting e GPS Spoofing;", value="Nada de discussão ou conversas relacionadas com ações que prejudicam outros jogadores;", inline=False)
    embed.add_field(name="A administração poderá moderar à sua discrição", value="Por vezes as regras não englobam todas as situações e como tal, a equipa de administração poderá moderar determinados assuntos que possam determinar como impróprios. Como tal, bom senso nas interações com os restantes membros da comunidade.", inline=False)
    await message.channel.send(
        content='Bem vindos ao Discord _Pokemon Go Leiria_, um projecto disponibilizado por **PoGoLeiria**.\nO objectivo desta comunidade é dinamizar a comunidade local e oferecer notificações **GRATUITAMENTE** sobre o aparecimento de novas _RAIDS_ e _SPAWNS POKÉMON_, a tempo real, nas zonas centro de **Leiria** e da **Marinha Grande**.\nExiste juntamente a estas notificações, um mapa em **https://pogoleiria.pt**, que se atualiza em tempo real e contém toda a informação disponibilizada pelo scanner pokémon.\nPor fim, temos também um bot complementar, que integra com parte das funcionalidades do scanner, de modo a facilicar o sistema de scan de pokémon para os membros da comunidade.\n\n_ _\n',
        embed=embed
    )
    await prepare_view_roles_teams(message.channel)