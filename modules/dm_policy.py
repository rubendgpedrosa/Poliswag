"""Who Poliswag still answers once the channel is gone.

Every command here was written for the server. The public ones lean on
the channel they live in to decide their audience; the admin ones check
an author id, which a DM satisfies exactly as well as the mod channel
does. Neither is a gate on DMs, so without this a stranger who opens one
gets `!questleiria` and `!accounts` for free.

Trades is the exception, and a deliberate one: `!trades` and `!resumo`
are advertised as DM commands and carry their own membership check.
"""

from modules.config import Config

DM_EXEMPT_COGS = frozenset({"Trades"})


def may_run_in_dm(ctx) -> bool:
    """Should this command run? Logs the ones it turns away.

    Answers True for anything in a guild — the policy only has an
    opinion about DMs, and each command keeps whatever gate it already
    carries either way.
    """
    if ctx.guild is not None:
        return True

    cog_name = ctx.cog.qualified_name if ctx.cog else None
    if cog_name in DM_EXEMPT_COGS:
        return True

    admin_ids = getattr(ctx.bot, "ADMIN_USERS_IDS", None) or []
    if ctx.author.id == Config.MY_ID or str(ctx.author.id) in admin_ids:
        return True

    ctx.bot.utility.log_to_file(
        f"[DM] @{ctx.author} ({ctx.author.id}): blocked !{ctx.invoked_with}"
    )
    return False
