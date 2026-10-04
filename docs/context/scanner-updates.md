# Scanner updates and account sessions

`/usr/local/sbin/scanner-update` checks registry manifests and prepares image updates without restarting services. Source: `/root/Poliswag/scripts/scanner-update`. Nightly schedule: `/etc/cron.d/scanner-update`, 05:10 UTC, after the backup and Diadem update. Owner notices use the existing `dm_owner.py` embed helper; unchanged pending updates are not repeatedly announced.

| Services | Policy |
| --- | --- |
| Dragonite, Admin, Golbat, Rotom-NG | Download immutable images; prepare separate version overrides |
| Poracle, Lork, Koji, Fletchling, Grafana | Download immutable images; await manual deployment |
| Scanner MariaDB, Diadem MariaDB | Download immutable patch images; automatically apply during the next orderly host shutdown/reboot |
| Tileserver | Report only; preserve the configured pin |

Dragonite selects the highest numeric `dragonite-vX.Y.Z-testing` tag and never changes release channels or downgrades. The script pulls by digest and retains `scanner-staged/*` and `scanner-previous/*` tags. It preserves live `main`/`latest` tags and all live Compose files. A minimum 3 GiB free-space check precedes downloads. Overlapping runs are excluded with a file lock.

Commands: `scanner-update --check` queries registries without writing, downloading, or messaging; `scanner-update` stages updates; `scanner-update --notify` also sends changed results to the owner; `scanner-update --status` prints saved state; `--services dragonite admin golbat rotom-ng` restricts the run.

State and complete Compose overrides: `/var/lib/scanner-update/{state.json,unonwhash.staged.json}`. A separate root-owned `/root/unonwhash/scanner-updates.staged.json` contains only the four scanner services and is visible through Poliswag's existing read-only mount. Applied pins remain in that override, so a later recovery cannot revert to the older base Compose image. Overrides contain image references only. Logs: `/var/log/scanner-update.log`.

## Database maintenance

Both MariaDB updates are downloaded within their existing version lines (`10.11` and `11.4`). `scanner-db-shutdown.service` automatically applies downloaded, pending database updates during an orderly host shutdown/reboot, while Docker and networking are still available. Starting the hook does nothing; its `ExecStop` runs `/usr/local/sbin/scanner-db-shutdown --shutdown`. The helper verifies that systemd has an actual shutdown/reboot job, so restarting Docker or manually stopping the hook does not trigger database updates. Forced power loss cannot run the hook.

For each running database it takes a fresh, completed SQL backup in `/root/backups/scanner-database-updates/`, validates the live Compose image and downloaded patch, then recreates only that database using an image-only override with `--no-deps --no-build --pull never`. It waits for authenticated `SELECT 1` before marking the patch applied. The normal MariaDB tag is then moved to the applied image, so a later plain Compose deployment uses that patch too. Docker performs the normal final shutdown and starts the updated containers when the host returns.

Already stopped databases are left stopped. Missing downloads, failed backups, changed image/version configuration, cancelled shutdowns, or an active staging lock defer application. A failed health check stays marked unconfirmed rather than triggering an automatic image downgrade against possibly migrated data. Results and backup paths are saved in `state.json`; the owner receives an outcome embed while networking remains available. Shutdown can take longer while backups and readiness checks finish; the hook has a 15-minute stop timeout.

Preview without applying: `scanner-db-shutdown --check`. Logs: `journalctl -u scanner-db-shutdown.service`. The nightly downloader itself never starts or restarts databases. Scanner recovery and `docker compose down` are separate from this host shutdown hook.

## Recovery protection

Every production `StackRecovery` operation that would reset Dragonite, Rotom, or the phone's scanner apps first queries Dragonite `/accounts/stats` and Rotom `/api/status` directly. It requires valid integer `in_use == 0` and total `worker_in_use_count == 0`. Unavailable, malformed, negative, or nonnumeric data defers recovery. Existing account-monitor display fallbacks are not used as evidence of zero sessions.

A deferral posts one moderation embed per episode and does not consume the recovery rung; the next scheduler tick checks again. New versions do not trigger recovery. During an existing genuine outage, once the guard passes, recovery may use the prepared scanner override. It keeps the existing recovery service selection (`dragonite rotom-ng`), `--no-deps`, and adds `--pull never`: Admin and Golbat are not restarted merely because updates exist. The database and map are not added to recovery.

After recreating controllers and waiting the existing 15 seconds, recovery checks usage again before resetting phone apps, because accounts may already have logged in. If status remains unknown, intervention may be needed rather than forcing a reset. These are checks immediately before operations, not a transaction that freezes Dragonite's account allocation. Login throttling and account rotation settings are unchanged.

Diadem keeps its existing independent updater. Tests use mocked subprocesses, HTTP and account data; they never restart production scanners.

## Preview operational embeds

`python3 scripts/preview_operational_embeds.py` renders distinct operational message variants into `logs/operational-embed-preview.json` using simulated events. `--send` sends them only to the owner (`MY_ID`), numbered and clearly marked as tests; mentions are disabled. It captures production notification code with mocked registry checks, database deployment and account/device data. The Diadem and host-script examples execute only extracted message-formatting statements. No service restart, database update, host shutdown, or Home Assistant notification is triggered. The report records Discord message IDs after delivery.
