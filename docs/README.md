# Poliswag documentation

Map of everything under `docs/`. Start with [`context.md`](context.md) — the orientation you read every session. This file is where to find the rest; open a topic only when the task touches it.

Repo-level: [`../README.md`](../README.md) (setup, `make` targets, commands, project layout).

## Core

| File | Read when |
|---|---|
| [`context.md`](context.md) | every session — what it is, stack, entry point, module/cog map, databases, conventions |

## Context topics (`docs/context/`)

| File | Read when |
|---|---|
| [`modules.md`](context/modules.md) | a module's behaviour, limits or rationale |
| [`cogs.md`](context/cogs.md) | a command's syntax, auth or behaviour |
| [`config.md`](context/config.md) | endpoints, env vars, dev vs prod |
| [`conventions.md`](context/conventions.md) | permissions/`!help`, `!notify` refs, migrations, scheduler, copy |
| [`database.md`](context/database.md) | table columns, or reading scanner/Dragonite/Poracle tables |
| [`quests.md`](context/quests.md) | quest commands, the `quests.json` export contract, quest maps |
| [`trades.md`](context/trades.md) | `!pokedex` codes, the digest, trade DMs/announcer, shared trade tables |
| [`hundo-alerts.md`](context/hundo-alerts.md) | the 100IV DMs, their Poracle rules/template, or the site's switch |
| [`events.md`](context/events.md) | event ingestion, Lisbon-time storage, CONVIVIO delivery/acks, repair tool, tests |
| [`event-panel.md`](context/event-panel.md) | `!eventpanel`, the Eventos role button, its @everyone behaviour |
| [`moderation.md`](context/moderation.md) | the `ignorar-este-canal` trap: ban/purge/unban, invite DM |
| [`error-review.md`](context/error-review.md) | the host cron that has Claude review `logs/error.log` daily |
| [`host-ops.md`](context/host-ops.md) | the host watchdog restarting Poliswag, `scripts/dm_owner.py`, backup-failure DMs |
| [`scanner-updates.md`](context/scanner-updates.md) | host-side scanner update staging and session-protected recovery |
| [`shiny.md`](context/shiny.md) | `!shiny`, the host linker and its guards/breaker, shiny alert DMs |
| [`shiny-access-requests.md`](context/shiny-access-requests.md) | shiny-map access request embeds and moderator decisions |
| [`shundo-mentions.md`](context/shundo-mentions.md) | per-player Shundo channel mentions and Poracle templates |
| [`spawn-history.md`](context/spawn-history.md) | raw spawn retention, unfiltered leaderboards, JSON export and coverage gaps |
| [`agents.md`](context/agents.md) | owner-requested Claude/Codex jobs, host executor and review worktrees |
| [`background-predictions.md`](context/background-predictions.md) | background-based shiny predictions |
| [`alert-noise-cleanup.md`](context/alert-noise-cleanup.md) | alert-noise work (2026-10-15) |
| [`pokedex-validation-alerts.md`](context/pokedex-validation-alerts.md) | Pokédex validation owner alerts |
| [`adb-server.md`](context/adb-server.md) | the shared ADB server |
