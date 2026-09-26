"""Who may run a command: members, mods (ADMIN_USERS_IDS) or the owner (MY_ID).

A refused command raises CheckFailure, which says nothing to the caller
unless its cog answers it. `!help` reads these same predicates
(`command.checks`) to decide which section a command belongs in, so a
command gated here is hidden from, and refused to, the same people.
"""

from discord.ext import commands

from modules.config import Config


def is_owner(ctx) -> bool:
    return str(ctx.author.id) == str(Config.MY_ID)


def is_mod(ctx) -> bool:
    admin_ids = getattr(ctx.bot, "ADMIN_USERS_IDS", None) or []
    return is_owner(ctx) or str(ctx.author.id) in admin_ids


def owner_only():
    return commands.check(is_owner)


def mods_only():
    return commands.check(is_mod)
