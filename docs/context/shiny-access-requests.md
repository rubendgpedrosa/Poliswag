# Shiny-map access moderation

The website's pending shiny-map access requests can be sent as one embed per
request to `MOD_CHANNEL_ID`, with **Aceitar** and **Rejeitar** buttons.
Owner `MY_ID` and `ADMIN_USERS_IDS` may decide requests in that guild channel.
There are no mentions or pings. The profile link lets moderators inspect the
requesting player.

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
and decision and removes the buttons. Database failures leave buttons available
for retry. If the message edit fails after commit, the private response explains
that the decision was saved; old buttons cannot apply the decision again.

## Activation requires explicit deployment authorization

These source changes do not activate the new notifier. The installed
`pokedex-validation-alert.service` continues calling the unchanged old DM
script until rollout. On an explicitly authorized rollout, restart Poliswag to
load the new cog first, install the updated source service unit and reload
systemd. Keep the existing timer: its service now invokes
`python -m scripts.pokedex_moderation_alert` instead of the owner-DM module.
Do not add a second timer or run both notifiers. Do not invoke the new module
as a test against live Discord; focused tests mock delivery and database writes.
