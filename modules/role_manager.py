import discord

from modules import scan_areas

_TEAM_ROLES = ["Instinct", "Mystic", "Valor"]

# Roles auto-granted to first-time users (len(roles) == 1 means only @everyone).
# The per-area alert roles come from the registry, so activating an area
# lights its role up everywhere; these are the ones that are not areas.
_EXTRA_NOTIF_ROLES = ["AlertasRaids", "AlertasPvP", "Remote"]


def notif_roles():
    return scan_areas.alert_roles() + _EXTRA_NOTIF_ROLES


class RoleManager:
    async def response_user_role_selection(self, interaction):
        # Acknowledge first: Discord voids the interaction after 3s, and
        # toggle_role can take longer (a new member gets every Alertas* role).
        await interaction.response.defer()
        await self.toggle_role(interaction.data["custom_id"], interaction.user)

    async def toggle_role(self, role, user):
        role_obj = discord.utils.get(user.guild.roles, name=role)
        if not role_obj:
            return

        if role_obj in user.roles and role not in _TEAM_ROLES:
            await user.remove_roles(role_obj, atomic=True)
            return

        if role_obj not in user.roles:
            if len(user.roles) <= 1:
                await self._add_default_notif_roles(user)
            await self.remove_team_roles(role, user)
            await user.add_roles(role_obj, atomic=True)

    async def _add_default_notif_roles(self, user):
        """Grant all notification roles to a brand-new member (only @everyone so far)."""
        for name in notif_roles():
            role_obj = discord.utils.get(user.guild.roles, name=name)
            if role_obj is None:
                continue
            await user.add_roles(role_obj, atomic=True)

    async def remove_team_roles(self, role, user):
        if role not in _TEAM_ROLES:
            return
        for other in _TEAM_ROLES:
            if other == role:
                continue
            role_obj = discord.utils.get(user.guild.roles, name=other)
            if role_obj and role_obj in user.roles:
                await user.remove_roles(role_obj, atomic=True)
