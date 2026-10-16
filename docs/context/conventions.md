# Conventions (full)

The core `docs/context.md` keeps the short version; this is the complete list.

- All modules receive `poliswag` (the bot instance) and access services via `self.poliswag.<module>`.
- Admin guard: `str(ctx.author.id) in self.poliswag.ADMIN_USERS_IDS` or `cog_check`.
- Three audiences: members, mods (`ADMIN_USERS_IDS`), owner (`MY_ID`), via `modules/permissions.py` (`is_mod`/`is_owner`; `mods_only()`/`owner_only()` for single commands). `!help` (`modules/help_command.py`) sections by the same gates: `_OWNER_COGS`, any `cog_check` (mods), or those predicates in `command.checks`. A command gated only inside its body shows to members — add the decorator. `!help` deletes its command message (in `prepare_help_command`). Tags go through `Mentions` before command dispatch: owner requests privately, other server members get fixed public replies; a bare mention greets instead of showing help. See `agents.md` for the data boundary and safeguards.
- `!notify` **ref** resolution: `#mention` → raw id → exact name → `%-<suffix>` LIKE; category suffixes (`raros`, `100iv`, `0iv`, `uteis`) fan out to `leiria-<suffix>` and `marinha-<suffix>`.
- `_PAIRED_PREFIXES = ("leiria-", "marinha-")` drives fan-out logic in `notifications.py`.
- Embed color: `Config.EMBED_COLOR = 0x4169E1`. Automated notices (owner DMs, mod-channel alerts, host scripts) go out as embeds, never plain text; plain text is for replies inside a flow (e.g. `!stats` follow-ups).
- Notification copy uses European Portuguese (PT-PT): describe shutdown/reboot as "desligar ou reiniciar o servidor", use natural singular/plural forms, and keep technical identifiers only where needed to identify a service or command.
- Tests under `tests/` (pytest). Run via `make test` inside Docker.
- Logging: `utility.log_to_file(msg, level)` → `logs/actions.log` + `logs/error.log`.
- Migrations: `setup_hook` replays every `migrations/*.sql` on each start (`modules/migrations.py`), before cogs load. No tracking table, so every statement must be `CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`; `test_every_migration_is_rerunnable` enforces it.
- Scheduler: a tick step taking ≥60s is logged as ERROR (`_run_tick_step`) so stalls reach the owner's daily error digest DM. Each tick ends by touching `logs/heartbeat` for the host watchdog (`docs/context/host-ops.md`).
