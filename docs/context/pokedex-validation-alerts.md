# Pokédex validation owner alerts

The host `pokedex-validation-alert.timer` runs every minute. Its oneshot service
executes `python -m scripts.pokedex_validation_alert` in the existing Poliswag
container, so deployment needs no bot restart. Unit sources live in `scripts/`.

Reads `Config.DB_POGOLEIRIA.trade_player` using precisely the website's pending
shiny-access criteria: active member, shiny_access=0, non-null request timestamp.
Includes requests caused by a changed Friend Code. It never approves access.

New (Discord ID, request timestamp) pairs POST to the existing
OWNER_ALERT_ENDPOINT. Home Assistant's "Poliswag: owner alerts" automation sends
these to Ruben's iPhone. The message names up to five players, gives the total
queue count, and includes the /pokedex/procurar approval-page URL. One tag groups
these notifications. No reminders for unchanged requests.

The watermark is `logs/pokedex-validation-alert.json`, atomically written with
mode 600 after a successful webhook response, retained across container/host
restarts. Resolved entries are pruned. Delivery failures fail the service and
retry next minute without advancing the watermark. As with other webhooks, a
crash after delivery but before the write can produce a duplicate. HTTP success
confirms HA acceptance, not phone receipt. No Discord fallback for this poller.

Operations: `systemctl status pokedex-validation-alert.timer`,
`journalctl -u pokedex-validation-alert.service`.
Disable with `systemctl disable --now pokedex-validation-alert.timer`.
