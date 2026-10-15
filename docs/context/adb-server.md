# Shared ADB server

The host service `adb-server.service` owns the foreground ADB server on
localhost:5037. Both `rustdesk-android-watchdog.service` and
`shiny-linker.service` require it through `adb-server.conf` drop-ins.

On 2026-10-15 the RustDesk oneshot was starting ADB in its own cgroup.
Systemd removed that daemon when the watchdog finished, breaking concurrent
player-ID association. ADB logs showed fresh server PIDs every minute and
replacement daemons at both linker failures. The game itself stayed running.

Source: `scripts/adb-server.service` and `scripts/adb-server-dependency.conf`.
Install the unit under `/etc/systemd/system/` and the dependency file as
`/etc/systemd/system/{rustdesk-android-watchdog,shiny-linker}.service.d/adb-server.conf`.
Run `systemctl daemon-reload` and enable `adb-server.service`.
A pre-existing unmanaged ADB server must release port 5037 before starting
the managed service; do this only while the linker is inactive.

Check `systemctl status adb-server.service`, the server PID/cgroup, and
`journalctl -u adb-server.service`. The PID must survive watchdog runs.
The linker remains disabled until its database switch is explicitly restored.

Recovery verified on 2026-10-15 at 12:17 UTC: the monitored linker run
resolved all four queued players (4 linked, 0 failed, 0 remaining). A manually
triggered RustDesk watchdog run during association did not replace the ADB
server, which remained at zero restarts; the game PID also stayed unchanged.
The linker switch was restored and the ten-minute timer remains enabled.
