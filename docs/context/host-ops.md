# Host-side operations around Poliswag

Things that run on the host, outside the container, because they must work when the bot doesn't.

| Piece | What |
|---|---|
| `scripts/notify_owner.py "msg" ["title"]` | Phone notification for the owner through Home Assistant (`OWNER_ALERT_ENDPOINT` in `.env`), falling back to `dm_owner.py` when HA doesn't answer. Stdlib only; needs no running bot. Used by the two below. |
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
