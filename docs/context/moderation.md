# Moderation: trap channel (`cogs/moderation.py`)

### Trap channel removal policy (2026-09-26)

`cogs/moderation.py` handles `TRAP_CHANNEL` (`ignorar-este-canal`). It already requested a server-wide 24-hour message purge when banning. It now immediately calls `guild.unban` after a successful ban, before saving counters or updating messages. This removes the member but permits rejoining by invite; it does not restore membership or roles. Existing bans are not retroactively lifted. Admin/bot exemptions remain unchanged.

Before the ban it DMs the member the rejoin invite (`_TRAP_REJOIN_INVITE`, `discord.gg/pASCYbp`) while they still share the guild; closed DMs are ERROR-logged and never block ban/unban, and MOD_CHANNEL shows whether the invite went out. The warning describes removal, cleanup, and rejoining. MOD_CHANNEL reports ban and unban separately; failed unbans are ERROR-logged and explicitly require manual removal. Only Discord's Unknown Ban (10026) counts as an already-completed unban; other HTTP failures remain failures. Counter errors cannot prevent an unban/report, and report fields cap at Discord's limit. 57 moderation tests cover the sequence, 24-hour purge, failed ban/unban, database failure, and warning text. No real member was banned or unbanned during testing.

## Deleted-message log (`on_message_delete`)

Posts removed messages (non-admin, outside MOD/QUEST/TRAP channels, not command invocations or a bare @Poliswag — the bot deletes those itself) to MOD_CHANNEL. Attachment URLs die with the message, so each attachment ≤10 MB is read from Discord's media cache (`read(use_cached=True)`) right away and re-uploaded with the log; the first image becomes the embed image (`attachment://`). Anything that can't be fetched is listed as `❌ … (não recuperado)`. Only messages still in the bot's cache trigger the event at all.
