# Host-side operations around Poliswag

Things that run on the host, outside the container, because they must work when the bot doesn't.

| Piece | What |
|---|---|
| `scripts/dm_owner.py "msg"` | DMs `MY_ID` via Discord REST with the bot token from `.env`. Stdlib only; needs no running bot. Used by the two below. |
| `scripts/watchdog.sh` | Run every minute by `/etc/systemd/system/poliswag-watchdog.{timer,service}`. If the container is running, has been up ≥10 min, and `logs/heartbeat` is ≥10 min old, it `docker restart`s Poliswag and DMs the owner. A stopped container is left alone (stopped on purpose). Log: `journalctl -t poliswag-watchdog`. |
| `logs/heartbeat` | Written at the end of every scheduler tick (`cogs/scheduled.py` `_write_heartbeat`, path `Config.HEARTBEAT_FILE`). Tests redirect it to a tmp path. |
| `/root/db-backup.sh` | Nightly 04:30 dump (not in this repo). On failure it now DMs the owner through `dm_owner.py` as well as logging to `/root/backups/db-backup.log`. |

**Why:** Docker's `unless-stopped` only recovers a process that exits; a hung bot stays "Up", and it is the bot that sends every other alert (`modules/site_health.py`, tracking health). The systemd units and `/root/db-backup.sh` are not in git: recreate them from this table if the host is rebuilt.
