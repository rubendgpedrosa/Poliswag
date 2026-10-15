# Host-side operations around Poliswag

Things that run on the host, outside the container, because they must work when the bot doesn't.

| Piece | What |
|---|---|
| `scripts/notify_owner.py [--discord] "msg" ["title"]` | Pokémon GO callers use `--discord` for private owner DMs. The generic backup caller retains Home Assistant with Discord fallback. Stdlib only; needs no running bot. |
| `scripts/dm_owner.py "msg" ["title"]` | DMs `MY_ID` an embed (msg = description, `EMBED_COLOR`) via Discord REST with the bot token from `.env`. The fallback for `notify_owner.py`; also used by `/usr/local/sbin/diadem-update`. |
| `scripts/watchdog.sh` | Run every minute by `/etc/systemd/system/poliswag-watchdog.{timer,service}`. If the container is running, has been up ≥10 min, and `logs/heartbeat` is ≥10 min old, it `docker restart`s Poliswag and alerts the owner (`notify_owner.py`). A stopped container is left alone (stopped on purpose). Log: `journalctl -t poliswag-watchdog`. |
| `logs/heartbeat` | Written at the end of every scheduler tick (`cogs/scheduled.py` `_write_heartbeat`, path `Config.HEARTBEAT_FILE`). Tests redirect it to a tmp path. |
| `/root/db-backup.sh` | Nightly 04:30 dump (not in this repo). On failure it alerts the owner through `notify_owner.py` as well as logging to `/root/backups/db-backup.log`. |
| `/usr/local/sbin/scanner-update` | Nightly 05:10 UTC via `/etc/cron.d/scanner-update`: stages immutable scanner images and DMs changed results as embeds. Source: `scripts/scanner-update`. Database updates automatically apply during host shutdown/reboot; tileserver is report-only. See [scanner-updates.md](scanner-updates.md) for account-session guards and recovery integration. |
| `/usr/local/sbin/scanner-db-shutdown` | `scanner-db-shutdown.service` applies downloaded MariaDB patches during orderly host shutdowns/reboots, with fresh backups and authenticated readiness checks. Does nothing when the service starts, Docker restarts, or the hook is manually stopped. Source and unit: `scripts/scanner-db-shutdown{,.service}`. |
| `python -m scripts.shiny_linker` | `shiny-linker.timer` (every 10 min) → `shiny-linker.service`, venv `/opt/shiny-linker/venv`. Resolves pending `!shiny` friend codes through one scanner account; does nothing unless every guard holds; switches itself off and alerts the owner if the hook or the game misbehaves. `--check` prints the guards, `--validate` re-checks the hook after a game update. Units: `scripts/shiny-linker.{service,timer}`. See [shiny.md](shiny.md). |

**Why:** Docker's `unless-stopped` only recovers a process that exits; a hung bot stays "Up", and it is the bot that sends the tracking-health alert and the owner notices. The systemd units and `/root/db-backup.sh` are not in git: recreate them from this table if the host is rebuilt.

The shared ADB daemon runs under its own persistent systemd service; the
RustDesk watchdog and shiny linker depend on it. Installation and diagnosis:
[adb-server.md](adb-server.md).

Pokémon GO operational alerts use private owner Discord DMs (2026-10-15):
map outage/recovery, shiny-linker failures, bot watchdog and
Pokédex approval requests. Map outages send one down and one recovery DM per
episode, instead of repeated phone pushes. Home Assistant's map webhook now
only updates `input_boolean.pokemon_map_down`. General backup and household
notifications retain their existing routes.


## Website monitoring to Discord

CT107 Grafana routes alerts whose `host` matches `pogoleiria.pt` or its
subdomains to the `PoGoLeiria Discord` contact point, after the dashboard-only
mute rule and before the general critical route. Other hosts retain Home
Assistant delivery. The provisioning file is
`/etc/grafana/provisioning/alerting/home-notifications.yaml` on CT107.

The contact posts to CT105 `http://192.168.1.253:8990/alerts`, handled by
`monitoring-discord-relay.service` (source in `scripts/`). The service reuses
Poliswag's Discord credentials without needing the bot process. The shared
bearer secret lives only in root-owned mode-0600
`/etc/poliswag-monitoring-relay.env` and Grafana's protected provisioning file.
Requests are limited to CT107's IP. Never commit the secret.

The relay persists delivered firing/recovery transitions in
`logs/monitoring-discord-relay.json`, so Grafana reminders and relay restarts
do not repeat unchanged alerts. Failed deliveries return a retryable error;
a recovery without a recorded outage is silent. Generic datasource errors
without a `host` label continue through general home monitoring. Inspect
`journalctl -u monitoring-discord-relay.service` for delivery failures.

Verification on 2026-10-15 used mocked Discord delivery tests, a rejected
unauthenticated HTTP request and an authenticated historical recovery (200,
no DM), plus inspection of Grafana's loaded receiver and route matchers. No
synthetic test message was sent to Discord. The bot was restarted to load
its changed routing and its scheduler heartbeat resumed.

Alert noise policy and the Home Assistant reminder schedule:
[alert-noise-cleanup.md](alert-noise-cleanup.md). Traffic silence is logged as
information and no longer sends owner alerts. Related website checks are grouped
in one Discord message with the affected vantage and endpoint details.
