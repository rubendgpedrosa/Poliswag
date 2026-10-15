# Shiny-map access moderation

The website's pending shiny-map access requests can be sent as one embed per
request to `MOD_CHANNEL_ID`, with **Aceitar** and **Rejeitar** buttons.
Owner `MY_ID` and `ADMIN_USERS_IDS` may decide requests in that guild channel.
There are no mentions or pings. The profile link lets moderators inspect the
requesting player. Each embed shows the member's server name, Discord account,
avatar and request date. The notifier queries guild membership only for unsent
requests, preferring the server nickname and server avatar over global identity.
If Discord cannot provide membership, the stored trade-player identity is used.
Discord CDN avatars use small static WebP images (128px); default avatars retain
their PNG asset. Invalid/non-HTTPS stored avatar URLs are omitted.
Request dates are interpreted as UTC and rendered using Discord timestamps in
the reader's timezone.

## Delivery and decisions

`scripts/pokedex_moderation_alert.py` replaces the old owner-DM notifier.
The separate durable `logs/pokedex-moderation-alert.json` state causes pending
requests previously announced by DM to appear in the moderation channel.
Each successful post is saved immediately, so a later failed post retries only
unsent requests. Resolved requests are pruned; a new timestamp is a new request.
As with the previous notifier, a process death between Discord accepting a post
and saving the state may cause a duplicate.

`cogs/shiny_access_requests.py` handles custom IDs through `on_interaction`,
so buttons survive bot restarts without an in-memory view. IDs bind the Discord
player ID to the full microsecond request timestamp. A short-lived transaction
locks the pending player row and checks that timestamp before writing anything;
stale, departed or resolved requests do not change the database.

Approval mirrors the website: grants access, enables the map, adds Shiny to
collecting, clears the request/relink flag, preserves notification preferences,
normalizes/synchronizes zones and increments the settings revision. Missing or
failed associations, or a changed Friend Code, queue the same link reset as the
website in that transaction. Existing linked/pending associations are retained
unless relinking is required. Invalid Friend Codes cannot queue a new association.
Rejection clears the pending request while keeping map/access off, retaining the
relink marker for a future request, and increments the settings revision.
The normal scheduler handles channels and setup confirmation.

After committing, the bot edits the original embed with the moderator's identity
and decision and removes the buttons. The original identity, avatar, fields and
request timestamp remain intact; two fields record who accepted/rejected the
request (server name and Discord account) and when the transaction completed.
The decision date is a Discord timestamp, separate from the request date.
Database failures leave buttons available for retry. If the message edit fails after commit, the private response explains
that the decision was saved; old buttons cannot apply the decision again.

## Runtime and rollout

The installed moderation notifier runs through the existing
`pokedex-validation-alert.timer` and service, invoking
`python -m scripts.pokedex_moderation_alert`. Its source is bind-mounted, so
future timer executions use notifier/template changes without a bot restart.
Cog changes require an explicitly authorized Poliswag restart to load.
Do not add a second timer or run both notifiers. Do not invoke the notifier
as a test against live Discord; focused tests mock delivery and database writes.
A requested owner-DM preview can render the shared payload without moderation
buttons or pending-request database/state changes.
