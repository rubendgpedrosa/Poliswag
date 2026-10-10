"""Is this Discord user in a guild the bot serves?

Commands that work in DMs need it: a DM channel outlives the membership
that created it. Used by cogs/pokedex.py and cogs/shiny.py.
"""


def is_member(bot, user_id):
    """Reads the member cache rather than fetching: `Intents.all()` keeps it
    populated, and a command shouldn't wait on an API call.

    Fails open when no guild has any member cached at all. An empty cache is
    the bot's problem, and locking every member out over it is worse than
    the hole it would close.
    """
    guilds = list(getattr(bot, "guilds", None) or [])
    cached = False
    for guild in guilds:
        if guild.get_member(user_id) is not None:
            return True
        cached = cached or bool(getattr(guild, "members", None))
    return not cached
