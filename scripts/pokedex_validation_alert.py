"""Poll the owner's Pokédex shiny-access queue; notify HA once per request.

Run by pokedex-validation-alert.timer inside the existing Poliswag container.
Failed delivery leaves the durable watermark unchanged for next-minute retry.
"""

import json
import os
from pathlib import Path
import urllib.request

SQL = """SELECT discord_id, COALESCE(trainer_name, display_name) AS name,
shiny_access_requested_at FROM trade_player
WHERE left_at IS NULL AND shiny_access = 0
AND shiny_access_requested_at IS NOT NULL
ORDER BY shiny_access_requested_at, discord_id"""
STATE = Path("/app/logs/pokedex-validation-alert.json")


def notify_pending(rows, state_path, send, base_url):
    previous = json.loads(state_path.read_text()) if state_path.exists() else []
    current = [
        [str(r["discord_id"]), str(r["shiny_access_requested_at"])] for r in rows
    ]
    new = [r for r, key in zip(rows, current) if key not in previous]
    if new:
        names = ", ".join(
            " ".join(str(r["name"] or "Treinador").split())[:80] for r in new[:5]
        )
        if len(new) > 5:
            names += f" e mais {len(new) - 5}"
        send(
            {
                "title": "Pokédex: validação pendente",
                "message": f'{names}. {len(rows)} pedido(s) de acesso shiny por validar.\n{base_url.rstrip("/")}/procurar',
                "tag": "poliswag-pokedex-validation",
            }
        )
    # Prune resolved requests so a later re-request can notify again. Atomic
    # replace and persistent bind mount preserve deduplication over restarts.
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(current))
    os.chmod(temporary, 0o600)
    temporary.replace(state_path)
    return len(new)


def main():
    from modules.config import Config
    from modules.database_connector import connect

    if not Config.IS_PRODUCTION:
        raise RuntimeError("Production only")
    endpoint = Config.ENDPOINTS.get("owner_alert")
    if not endpoint:
        raise RuntimeError("OWNER_ALERT_ENDPOINT is not configured")
    connection = connect(Config.DB_POGOLEIRIA, dict_rows=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(SQL)
            rows = cursor.fetchall()
    finally:
        connection.close()

    def send(body):
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Home Assistant returned {response.status}")

    count = notify_pending(rows, STATE, send, Config.POKEDEX_URL)
    if count:
        print(f"Notified owner of {count} new request(s); {len(rows)} pending")


if __name__ == "__main__":
    main()
