# Cog map (`cogs/`)

Full command table. The core `docs/context.md` keeps only the auth summary.

| Cog | Commands | Auth |
|-----|----------|------|
| `quests.py` | `!questleiria [area] [search]` / `!questmarinha`, `!scan`, `!exportquests` | `!scan`+`!exportquests` admin-only |
| `tracker.py` | `!track`, `!untrack`, `!tracklist`, `!untrackall`, `!tracked` | admin-only (`cog_check`) |
| `event.py` | `!eventtypes`, `!exclude`, `!include`, `!excludedlist`, `!excludeclear` | admin-only |
| `notifications.py` | `!notify channels\|list\|add\|remove\|enable\|disable\|register\|test` | admin-only |
| `accounts.py` | `!accounts` | `MY_ID` only (`cog_check`) |
| `container_manager.py` | `!container start\|stop`, `!status` | `MY_ID` only |
| `moderation.py` | Listeners: `on_interaction` (role buttons), `on_message_delete`, trap channel (see `moderation.md`) | — |
| `scheduled.py` | `!weeklydigest`, `!testevent HH:MM`; `@tasks.loop` every minute (version/quest-scan/events/workers/accounts/weekly-digest + 30-min safety-net quest export; the daily "erro(s) novo(s)" digest is DMed to `MY_ID` since 2026-09-26) | admin-only |
| `lures.py` | `!lures`, `!uselure USERNAME NUMERO` | admin-only (`cog_check`) |
| `webstats.py` | `!stats` / `!webstats`, `!stats export [7d\|30d\|Nd\|all]`, `!stats novocodigo` — answers by DM | `MY_ID` only (`cog_check`) |
| `pokedex.py` | `!pokedex` / `!trades` / `!trocas` (DM login code), `!resumo [dias]` (trade digest preview) | Members; DM-capable (own membership check) |
| `event_panel.py` | `!eventpanel` (publica o painel), `!eventpanel test` (DM de ensaio), `!eventpanel clear [confirm]` | `MY_ID` only (`cog_check`) |
| `announcements.py` | `!anunciar <texto>` — reposts the text (+ attachments) as typed in the announcements channel (`EVENT_PANEL_CHANNEL_ID`). Echoes a pings-off preview first; posts only on **Publicar** (author only, 5 min, one press), with `AllowedMentions.all()` so a typed `@everyone` pings. Deletes the command message once the preview is up (kept on errors); attachments are read into memory first so Publicar still has them | admin-only (`cog_check`) |
| `shiny.py` | `!shiny <código>` / `!shiny` / `!shiny remover`; listener for the alert verdict buttons | `MY_ID` only during the pilot (`owner_only`); built for members, works in DMs (own membership check) |
| `shiny_access_requests.py` | Listener for the shiny-map **Aceitar**/**Rejeitar** buttons on the moderation-channel embeds | `MY_ID` or `ADMIN_USERS_IDS`, only in `MOD_CHANNEL_ID` |
| `agents.py` | `!self`, `!ask`, `!code`, `!jobs`, `!job`, `!cancel`; explicit provider requests in owner DMs/mentions | `MY_ID` only; host worker, private queue, separate worktrees |
| `spawn_history.py` | `!spawnhistory`, `!spawnhistory export [hours]`, `!spawnhistory leaderboard [hours]`; independent 30-second collector | `MY_ID` only |
| `mentions.py` | Owner mentions delegate free-form requests privately; other members get jokes, public events or aggregate game numbers | Actual Discord identity; public Codex Luna medium without shell/history; fixed data allowlist and cooldowns |
