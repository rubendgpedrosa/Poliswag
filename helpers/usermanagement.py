import discord
from discord.ui import Button, View

async def prepare_view_roles_location(channel):
    buttonLeiria = Button(label="LEIRIA", style=discord.ButtonStyle.secondary, custom_id="AlertasLeiria")
    buttonMarinha = Button(label="MARINHA GRANDE", style=discord.ButtonStyle.secondary, custom_id="AlertasMarinha")
    buttonPvP = Button(label="PvP", style=discord.ButtonStyle.secondary, custom_id="AlertasPvP")

    await add_button_event(buttonLeiria)
    await add_button_event(buttonMarinha)
    await add_button_event(buttonPvP)

    view = View()
    view.add_item(buttonLeiria)
    view.add_item(buttonMarinha)
    view.add_item(buttonPvP)

    embed = discord.Embed(title="NOTIFICAÇÕES OPCIONAIS DO MAPA", description="Ao clickarem no botão correspondente, poderão ativar/desativar as notificações para as diferentes localidades.")
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

    embed = discord.Embed(title="SELEÇÃO DE EQUIPA", description="Ao clickarem no botão correspondente, poderão atribuir/remover a vossa equipa.")
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
    message = build_response_message(role)
    if roleToToggle:
        if roleToToggle in user.roles:
            await user.remove_roles(roleToToggle, atomic=True)
            return {'msg': message + " desativadas.", 'color': 0xff0000}
        else:
            await remove_team_roles(role, user)
            await user.add_roles(roleToToggle, atomic=True)
            return {'msg': message + " ativadas.", 'color': 0x00ff00}

def build_response_message(role):
    if role.lower() == "alertasleiria" or role.lower() == "alertasmarinha" or role.lower() == "alertaspvp":
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
