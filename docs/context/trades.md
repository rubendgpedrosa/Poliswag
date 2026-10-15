# Trades

Source: `modules/trade_digest.py`, `modules/trade_dm.py`, `modules/trade_announcer.py`, `modules/trade_codes.py`, `modules/trade_player_store.py`, `modules/trade_stats.py`, `modules/membership.py`, `cogs/pokedex.py`; tables from `migrations/010`–`015`. Site side: PoGoLeiria `docs/context/pokedex.md`.

## Ownership: the shared tables are the contract
- `pogoleiria.trade_player` — **Poliswag owns identity, `code_hash`, `code_issued_at`, `left_at` and the `shiny_access` default** (upsert on `!pokedex`, refresh on member update, leave/join listeners, `on_ready` reconcile). **The site owns `trainer_name`, `friend_code`, `note`, `collecting`, `show_costumes`, `trade_dms` and the hundo/shiny switches.**
- `pogoleiria.trade_entry` — site-written. One row per `(discord_id, list, category, pokemon_id, form_id)`; `list` is `have`/`want`, `category ∈ normal/hundo/lucky/shiny/xxl/xxs`, and `pokemon_id = 0` is the site's ANY_SPECIES (a category-only want).
- Poliswag bookkeeping: `pogoleiria.trade_notice` (announcer dedupe), `pogoleiria.trade_dm_notice` (DM dedupe), and `poliswag.poliswag.last_trade_notice_at` / `last_trade_digest_at` / `last_trade_dm_at` watermarks.
- There is no API between both sides: these tables are the whole contract. Readers use `connect(Config.DB_POGOLEIRIA, ...)` and keep the `pogoleiria` reads read-only.

## `!pokedex` login codes
- `cogs/pokedex.py` generates a code with `modules/trade_codes.py`: Crockford base32 (no I/L/O/U), 16 chars, 80 bits; only the SHA-256 is stored (`code_hash`). `apps/landing/lib/pokedex/code.ts` mirrors it, pinned to the same test vector.
- The DM carries a tappable link (`POKEDEX_URL/entrar/<code>?o=pokedex-login`) and then the bare code in a second message (long-press → Copy Text). Every `!pokedex` replaces the hash, ending older sessions.
- Command aliases `!trades` / `!trocas` still answer (kept out of `!help pokedex`). In a channel the invocation is deleted and a 15 s confirmation is posted; in a DM the code is the reply.
- `modules/membership.py` blocks non-members in DMs only (guild channels are proof; fails open when no guild cache exists). `on_member_remove` / `on_member_join` set/clear `left_at`; `reconcile()` on startup catches changes missed while the bot was down.

## The 09:00 digest
- `trade_digest.tick(now)` posts once a day from 09:00 (`DIGEST_HOUR`), using `last_trade_digest_at` as both "already posted today" and the read window; a restart later that morning stays quiet. The first-ever run sets the watermark and posts nothing.
- Silent when nothing was added. One embed in `TRADES_CHANNEL_ID`: fields "✨ Para trocar" / "🔍 Procurados", one line per Pokémon (`Poliwag · Shiny`), the name linked to `/pokedex/jogador/<id>?o=pokedex-resumo`, 🤝 when the entry already has a partner. Caps: 3 lines per player, 12 lines total, 1024-char field, "… e mais N"; big art when ≤2 entries.
- No pings (`AllowedMentions.none()`).
- `!resumo` (same cog) is the manual preview: `rows_since(days)` reads the same rows over a 1–30 day window and never touches the watermark, because a silent digest and a broken one look identical from the channel.

## Trade announcer
- `trade_announcer.tick()` posts matches as they happen in `TRADES_CHANNEL_ID`. It reads only **typed** `trade_entry` wants; the site no longer writes those, so it is normally quiet (the site's derived wants drive trade DMs instead).
- Reads with a 90 s cutoff; watermark `last_trade_notice_at`; `trade_notice` stops repeats. Groups by actor (whoever posted last), mentions the actor and up to 8 people; past 8 the post collapses to a count line. The title is a name, not `<@id>`; links carry `?o=pokedex-par`.

## Trade DMs
- `trade_dm.tick()` runs every minute. A player gets a DM when someone adds an unconditional spare they are missing (derived want: a `collecting` category with no `collection_entry` tick; costumes only when `show_costumes = 1`), **and** they have an unconditional spare that holder is missing. The spare's owner is not messaged.
- Legacy conditional spares (`trade_for_category IS NOT NULL`) are ignored. The 60 s cutoff lets the site's tick+spare transaction settle. `trade_player.trade_dms` (site switch, default on) gates it; `trade_dm_notice` prevents repeats; the watermark `last_trade_dm_at` advances only after a tick that finished everyone, at most 20 recipients per tick.
- Message: "Tens uma troca nova com …", per holder `Tem para ti: …` / `Quer de ti: …`, a profile link `?o=pokedex-dm`, and the opt-out hint (Pokédex › Perfil). DMs closed are logged and recorded (never retried forever).

## `!stats` trade numbers
- `modules/trade_stats.py` feeds the owner's `!stats` embed: entries/users/have/want counts for the window, plus the 100IV-DM availability summary (`active` / `leiria` / `marinha` / `waiting` / `dms_closed` / `unhealthy`) using `hundo_alerts`' own rule. It never exposes identities.
