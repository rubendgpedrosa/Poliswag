# Pokédex 100IV alerts — design

**Status:** approved 2026-09-24. Poliswag side first; the site's switch
follows. Tested on the owner's account before anyone else sees it.

## Goal

A player who collects 100IV in the Trades Pokédex gets a private Discord DM
when a 100IV spawns of a Pokémon they haven't ticked there.

## Decisions

| Question | Decision |
|---|---|
| Who | Trades players with `hundo` in `trade_player.collecting`, still in the server (`left_at IS NULL`), who switched it on |
| On/off | **Off by default for everyone.** A switch in the Pokédex settings; while testing it is shown only to Discord ids in the Trades setting `HUNDO_DM_TESTERS` |
| Delivery | Private DM (not a channel ping, not per-Pokémon roles: Discord caps a server at 250 roles) |
| Which spawns | Every 100IV spawn of a Pokémon they haven't ticked; no daily cap, no quiet hours |
| Area | Each player picks Leiria, Marinha Grande or both (default both) |
| Engine | **Poracle sends; Poliswag keeps each collector's Poracle rules in step with their Pokédex**, as `cogs/notifications.py` (`!notify`) does for channels |

Poracle already watches spawns (pushed by Golbat, seconds of latency),
resolves areas from its geofences and sends DMs with the channel alerts'
look. It signs in with Poliswag's bot token, so the DMs come from Poliswag.

## Data: five columns on `trade_player`

One migration (Poliswag `014`; the same re-runnable statement as a Trades
`db/` file when the site part lands).

| Column | Default | Written by | Meaning |
|---|---|---|---|
| `hundo_dms` | 0 | site | the switch |
| `hundo_areas` | `leiria,marinha` | site | SET of areas to alert on |
| `hundo_settings_at` | NULL | site | set on every change to the two above, and only then |
| `hundo_confirmed_at` | NULL | Poliswag | when the last confirmation DM was delivered |
| `hundo_dm_refused_at` | NULL | Poliswag | when Discord last refused a confirmation DM |

A dedicated stamp, not `updated_at`: that one moves on any edit (trainer
name, note, collections) and on Poliswag's own writes, which would send
confirmations nobody asked for and loop.

## Poliswag: `modules/hundo_alerts.py`

One scheduled step per minute, like `trade_dm.py`, in two phases.

### 1. Confirmations (the delivery check)

Poracle-NG does not record a refused DM, and Discord only reports a refusal
when a message is actually sent (opening a DM channel succeeds with DMs
closed). So Poliswag's own confirmation DM is the check.

- Due when `hundo_settings_at` is newer than both `hundo_confirmed_at` and
  `hundo_dm_refused_at`.
- The DM states the full current state, never a diff (several changes in one
  minute are read at once):
  - on: "100IV por DM: **ligado** · Leiria e Marinha Grande. Vais receber
    aqui os 100IV que te faltam na Pokédex." (or "só Leiria" / "só Marinha
    Grande")
  - off: "100IV por DM: **desligado**. Já não te enviamos 100IV."
- Delivered: `hundo_confirmed_at = NOW()`. Refused (`Forbidden`,
  `NotFound`): `hundo_dm_refused_at = NOW()`; not retried until the
  settings change again (saving them is the retry).
- The site shows "Não conseguimos enviar-te DMs…" when the refusal is newer
  than the confirmation.

### 2. Rule sync

A collector is **active** when: `hundo_dms = 1`, `hundo` in `collecting`,
`left_at IS NULL`, a delivered confirmation (`hundo_confirmed_at` not NULL)
and no refusal since it (`hundo_dm_refused_at` NULL or older).

For each active collector:

- **Wanted rules.** Every Pokédex tile missing at 100IV: rows of
  `poliswag.pokemon_name` (species at form 0 plus named forms; costumes only
  when `show_costumes = 1`) with no `collection_entry` tick in `hundo`. The
  same "missing" as `trade_dm.py`.
- **Forms.** In Poracle `form = 0` means any form, so:
  - a missing form-0 tile becomes a rule on the species' `defaultFormId`
    from the masterfile Poliswag already loads (Rattata → 45); a species
    with no forms stays at 0;
  - a missing named form becomes a rule on that form id.
  So an owned Alolan never alerts because the ordinary one is missing.
- **Rule shape.** Copied from the existing channel rules, with: `id` = the
  player's Discord id, `template = 'pokedex-100iv'`, `pokemon_id`, `form`,
  `min_iv = max_iv = 100`, `distance = 0` (area-based), `override_areas`
  from `hundo_areas` (`["leiria","marinhagrande"]`), everything else wide
  open, `profile_no = 1`, `clean = 0`, `ping = ''`. The area lives on the
  rules, not on the Poracle user, so a player's own Poracle alerts keep
  their own area.
- **Poracle user.** A `humans` row `discord:user` for the player, created if
  missing (API, as `!notify` creates channels, then started), with the same
  area as the rules. An existing human is left as it is.
- **Compare, then rebuild.** Read the player's current rows with that
  template; if the (pokemon_id, form) set or the area differs, one
  transaction on the `poracle` DB deletes their `pokedex-100iv` rows and
  inserts the wanted ones (the area is part of each row). Comparing actual
  rows makes every pass
  self-healing, with no stored hash.
- **Everyone not active** (switched off, refused, left, stopped collecting
  100IV) loses their `pokedex-100iv` rows. Their own Poracle alerts, other
  templates, are never touched, and neither is an existing Poracle user.
- **One reload.** `POST /api/reload` once per tick, only if something
  changed.
- **Poracle's alert limit** (`[alert_limits]`: 20 DMs per user per 240 s;
  10 breaches in 24 h stops the user). Our volume is ~30–46 100IV spawns a
  day across both areas, so it isn't expected to trigger. If Poracle has
  stopped a player (`humans.enabled = 0` not set by us), the sync leaves
  them stopped and logs it, rather than switching them back on.

### The alert DM (Poracle template)

A new DTS entry `id: pokedex-100iv`, `type: monster`, `platform: discord`,
in `/root/poracleng/config/dts.json` (not in git; back it up first):

- title "100IV que te falta: {{fullName}}", sprite thumbnail;
- CP, level, area;
- "até {{time}} · faltam {{tthm}} min";
- link `{{{reactMapUrl}}}?o=dm-100iv` (counts as its own origin in the
  site's stats).

## Rollout

1. Poliswag: migration 014, `hundo_alerts.py`, scheduler step, tests.
2. Poracle: the `pokedex-100iv` DTS entry (backup first), restart.
3. Owner test: owner adds 100IV to their collections on the site, then
   their row is switched on by SQL (`hundo_dms = 1`, both areas,
   `hundo_settings_at = NOW()`). Expect the confirmation DM on the next
   tick, their rules in Poracle, then real 100IV DMs. With nothing ticked
   that is ~35 a day until they tick what they own.
4. Site: the switch, area choice and refusal notice in "Perfil e outras
   opções", shown to `HUNDO_DM_TESTERS`; release by emptying it.

## Testing

Unit (pytest, mocked DB/Discord like `trade_dm.py`'s tests):
- missing tiles → rules, including default-form translation, named forms,
  species without forms, costumes vs `show_costumes`;
- wanted vs current → rebuild or skip; an area change alone triggers it;
- the human is created only when missing and never edited otherwise;
- who is active (switch, collecting, left, confirmed, refused ordering);
- confirmation due/not due; delivered vs refused writes; DM wording for
  on/off and each area choice;
- inactive players lose only `pokedex-100iv` rows; reload only on change;
- a stopped human is not re-enabled.
Live: the owner test above.

## Known costs

- Coupled to Poracle-NG's `monsters` schema; an upgrade that changes it
  fails the sync loudly (SQL error) rather than wrongly.
- One DM per spawn, no bundling.
- DMs closed after the last settings change go unnoticed until the player
  changes something again; alerts in between are lost for them. Accepted
  rather than sending unrequested messages.
- Early volume: 20–35 DMs a day for a collector missing most species
  (8 days of `pokemon_hundo_stats`, both areas); it falls as the Pokédex
  fills.

## Alternatives considered

- **Poliswag polls Golbat and DMs itself:** no rule copy, can bundle and
  see every refusal, but up to a minute late and all delivery rebuilt. The
  fallback if the confirmation check proves too blind.
- **Reading Poracle's logs for refusals:** they name the DM channel, not the
  player, and depend on the log format.
- **Pings instead of DMs** (mentions on the alert, a shared thread, a
  private thread each): Poracle can't mention specific people.
- **A role per Pokémon:** over the 250-role cap, clutters profiles, needs
  constant role sync.
