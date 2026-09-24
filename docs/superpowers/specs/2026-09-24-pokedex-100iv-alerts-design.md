# Pokédex 100IV alerts — design note

**Status:** on standby (2026-09-24). Direction agreed; not planned or built.

## Goal

A player who collects 100IV in the Trades Pokédex gets a private Discord DM
when a 100IV spawns of a Pokémon they haven't ticked there.

## Decisions so far

| Question | Decision |
|---|---|
| Who | Trades players with `hundo` in `trade_player.collecting`, still in the server (`left_at IS NULL`) |
| On/off | On by default for those players; a switch in the Pokédex settings turns it off (like `trade_dms`) |
| Delivery | Private DM (not a channel ping, not per-Pokémon roles: Discord caps a server at 250 roles) |
| Which spawns | Every confirmed 100IV spawn of a Pokémon they haven't ticked, no daily cap, no quiet hours |
| Area | Each player picks Leiria, Marinha Grande or both (default both) |
| Engine | **Poracle sends; Poliswag keeps each collector's Poracle rules in step with their Pokédex** |

## Why Poracle, and how it stays correct

Poracle already watches spawns (pushed by Golbat, so seconds of latency),
resolves areas from its geofences and sends DMs, with the same look as the
channel alerts. It signs in with Poliswag's own bot token, so DMs come from
Poliswag. What it can't know is the Pokédex, so Poliswag writes one rule per
missing Pokémon per collector (600–900 each today).

This follows `cogs/notifications.py` (`!notify`), which already registers
Poracle humans, writes rules and reloads Poracle for channels. The
differences: the target is a `discord:user`, the rules come from the Pokédex
rather than a moderator, and there are hundreds per person.

Rules that keep the copy honest:

- **Full rebuild, not incremental edits.** Per collector, one transaction on
  the `poracle` DB: delete the rows Poliswag manages, insert the current
  missing list, then `POST /api/reload` once. A failed pass is repaired by the
  next one. Skip the rebuild (and the reload) when the desired set hasn't
  changed, e.g. by hashing it per player.
- **Managed rows are marked.** Poliswag's rows use their own `template`
  (`pokedex-100iv`) and are the only rows it deletes or inserts, so a
  player's own Poracle alerts are never touched. That DTS template is also
  the DM's look, with the map link tagged `?o=dm-100iv`.
- **Rule shape:** `pokemon_id`, `form`, `min_iv = max_iv = 100`, other
  bounds wide open; area from the player's choice on their `humans` row
  (`leiria`, `marinhagrande`).
- **Forms (the one real piece of work).** In Poracle `form = 0` means any
  form. The Pokédex stores the ordinary form as 0 and only special forms
  (Alola, costumes) with their own id; Golbat reports the ordinary form with
  its own id (Rattata = 45). A missing ordinary form must become a rule on
  Golbat's ordinary form id (from the masterfile Poliswag already loads), and
  a missing special form a rule on that form, so an owned Alolan never
  alerts because the ordinary one is missing.
- **Humans.** Create each collector as a Poracle `discord:user` with their
  area, start/stop it with their switch (as `!notify` does for channels).
- **Refused DMs, shown on the site.** Recorded on `trade_player` (the table
  the site already reads), from two sources:
  - a welcome DM Poliswag sends itself when a collector is first switched on
    ("Vais receber aqui os 100IV que te faltam…"): if Discord refuses it,
    record it at once. This catches DMs closed from the start;
  - Poracle's own record on `poracle.humans` (`fails`, `admin_disable`,
    `disabled_date`), copied across on each sync pass. The Pokédex settings
    then say "Não conseguimos enviar-te DMs" and how to fix it.

## Known costs

- Coupled to Poracle-NG's `monsters` schema: an upgrade that changes it
  breaks the sync with a SQL error, which is loud rather than wrong.
- One DM per spawn; no per-minute bundling.
- Unverified: that Poracle-NG (the Go rewrite) actually increments `fails` /
  sets `disabled_date` when Discord refuses a DM, as the original Poracle
  did. Check first when resuming: a test user with DMs closed, then watch the
  row. Until confirmed, only the welcome-DM check is certain.
- Volume at the start: 20–35 DMs a day for a collector missing most species
  (measured over 8 days of `pokemon_hundo_stats`, both areas); it falls as
  the Pokédex fills.

## Alternatives considered

- **Poliswag polls Golbat and DMs itself:** no rule copy, can bundle and
  report refused DMs, but up to a minute late and all delivery rebuilt.
- **Pings instead of DMs:** mentions on the #*-100iv alert, a shared "Caça
  100IV" thread, or a private thread per collector. A private thread avoids
  closed-DM problems entirely, but Poracle can't mention specific people, so
  it would mean the polling approach.
- **A role per Pokémon:** over Discord's 250-role cap, clutters profiles,
  and needs constant role sync.

## Open when resumed

- Where the switch and area live in the Pokédex settings UI, and the columns
  on `trade_player` (the web app writes them, Poliswag reads them).
- DM template copy (Portuguese, English game terms) and the `pokedex-100iv`
  DTS entry, which lives in `/root/poracleng/config/dts.json` (not in git).
- How often the sync runs (every tick with a no-change skip is the default).
- The refused-DM column(s) on `trade_player`, and whether a refusal also
  stops the collector in Poracle until they fix it.
