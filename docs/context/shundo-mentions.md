# Shundo channel mentions

A predicted shiny with scanner IVs exactly 15/15/15 adds `**✨💯 SHINY 100% 💯✨** @here` to its
existing alert in that recipient's private Shiny channel. Prediction is
per player: another player's background-only 100% prediction does not ping.
Unknown IVs, non-100% shinies and DMs do not ping. Normal eligibility,
notification settings, locations and deduplication still apply.

`modules/shiny_alerts.py` selects `shiny-here` or `shiny-background-here`
only for channel destinations. The corresponding monster DTS entries live
in `templates/poracle/`. They preserve their ordinary templates' artwork and
buttons, adding only the fixed `template.content: "**✨💯 SHINY 100% 💯✨** @here"`. Poracle joins
arrays of strings when rendering DTS values, so an `allowed_mentions.parse`
array would become an invalid string. These templates omit `allowed_mentions`
and rely on Discord's default mention handling for regular bot channel
messages. The content is a fixed literal with no user or role mention text.
The fallback embed adds the same content with explicit
`discord.AllowedMentions(everyone=True, users=False, roles=False,
replied_user=False)` on the same message. Fallback DMs never include it.

Activation requires installing both new entries in
`/root/poracleng/config/dts.json` and calling `POST /api/dts/reload`, then
restarting Poliswag to load the selector. Poracle's Discord delivery must
preserve top-level `content` from the rendered DTS message and send
regular bot channel messages with Discord's default mention handling. Its test API destination selects the template and channel; content
cannot be overridden through the test destination. The bot needs Discord's
Mention Everyone permission in each target channel for `@here` to notify.
No separate ping messages or global-channel announcements are sent.
