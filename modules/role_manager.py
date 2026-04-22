import discord


class RoleManager:
    async def response_user_role_selection(self, interaction):
        await self.toggle_role(interaction.data["custom_id"], interaction.user)
        await interaction.response.defer()

    async def add_button_event(self, button):
        button.callback = self.response_user_role_selection

    async def restart_response_user_role_selection(self, interaction):
        await self.response_user_role_selection(interaction)

    async def toggle_role(self, role, user):
        roles_list = ["Instinct", "Mystic", "Valor"]
        notif_list = [
            "AlertasLeiria",
            "AlertasMarinha",
            "AlertasRaids",
            "AlertasPvP",
            "Remote",
        ]
        roleToToggle = discord.utils.get(user.guild.roles, name=role)
        if roleToToggle:
            if roleToToggle in user.roles and role not in roles_list:
                await user.remove_roles(roleToToggle, atomic=True)
            elif roleToToggle not in user.roles:
                if len(user.roles) <= 1:
                    for notifRolesToAdd in notif_list:
                        roleListNotif = discord.utils.get(
                            user.guild.roles, name=notifRolesToAdd
                        )
                        await user.add_roles(roleListNotif, atomic=True)
                await self.remove_team_roles(role, user)
                await user.add_roles(roleToToggle, atomic=True)

    async def remove_team_roles(self, role, user):
        roles_list = ["Instinct", "Mystic", "Valor"]
        if role not in roles_list:
            return
        roles_list.remove(role)

        for role_list in roles_list:
            roleToRemove = discord.utils.get(user.guild.roles, name=role_list)
            if roleToRemove in user.roles:
                await user.remove_roles(roleToRemove, atomic=True)
