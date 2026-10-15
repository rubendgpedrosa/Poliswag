# Shiny-map access moderation

The website's pending shiny-map access requests can be sent as one embed per
request to `MOD_CHANNEL_ID`, with **Aceitar** and **Rejeitar** buttons.
Owner `MY_ID` and `ADMIN_USERS_IDS` may decide requests in that guild channel.
There are no mentions or pings. The profile link lets moderators inspect the
requesting player. Each embed shows the member's server name, Discord account,
and avatar. The message creation time identifies when the request was posted;
there is no separate request-date field or pending embed timestamp. The notifier
queries guild membership only for unsent requests, preferring the server nickname
and server avatar over global identity.
If Discord cannot provide membership, the stored trade-player identity is used.
Discord CDN avatars use small static WebP images (128px); default avatars retain
their PNG asset. Invalid/non-HTTPS stored avatar URLs are omitted.

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

After committing, the bot edits the original embed and removes the buttons.
The player's two identity fields and avatar remain intact. The title states
whether access was accepted or rejected, with green/red coloring respectively.
The footer contains only the moderator's server display name; the embed's native
UTC timestamp records when the transaction completed. Discord displays the name
and localized decision date together in the footer. Old request-date/audit fields
are removed when editing previously posted requests.
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


## Failed Friend Code associations

The same notifier also posts a separate embed to moderation when an approved
player's Friend Code association fails. It shows the server name, Discord
account, avatar, profile link and a curated PT-PT reason. The native timestamp
in the "Associação falhada" footer records the failure time. It has no decision
buttons or mentions: approval has already happened, and the player can correct
the Friend Code or retry from their profile. Raw infrastructure errors and the
Friend Code itself are never included.

This flow joins the configured community and bot schemas. It includes approved,
present members even if they disabled Discord alerts or their map, so a recent
failure remains visible. Held, revoked and departed accounts are excluded.
Separate durable state in `logs/pokedex-link-failures.json` records Discord ID,
association-request timestamp and resolution timestamp. Each successful post
is saved immediately; timer retries and restarts do not repeat it, while a newly
failed attempt is notified again. Resolved failures are pruned. As with access
requests, a crash between Discord accepting the message and saving state can
duplicate a post. Both notification flows are attempted independently, so an
access-request delivery failure does not suppress failed-association delivery.
