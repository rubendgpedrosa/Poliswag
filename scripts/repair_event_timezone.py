"""Preview by default; --apply repairs times/posts; --resume BACKUP retries edits.

Stop the bot while applying database repairs. A saved journal can resume Discord
edits independently of the now-correct database rows. No new messages are sent.
"""

import asyncio
import json
import sys
from datetime import datetime, time
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import aiohttp
from modules.config import Config
from modules.database_connector import connect
from modules.utility import Utility
from modules.locale_pt import PT_MONTHS_SHORT


def plan_changes(rows):
    util = Utility.__new__(Utility)
    changes = []
    for row in rows:
        source = json.loads(row["extra_data"] or "{}")
        if not source.get("start") or not source.get("end"):
            continue
        start = util.format_datetime_string(source["start"])
        end = util.format_datetime_string(source["end"])
        if (str(row["start"]), str(row["end"])) == (start, end):
            continue
        old_start = datetime.fromisoformat(
            source["start"].replace("Z", "+00:00")
        ).strftime("%Y-%m-%d %H:%M:%S")
        old_end = datetime.fromisoformat(source["end"].replace("Z", "+00:00")).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        if str(row["start"]) not in (old_start, start) or str(row["end"]) not in (
            old_end,
            end,
        ):
            raise ValueError(f'Unrelated schedule change: {row["name"]}')
        changes.append({"row": row, "start": start, "end": end})
    return changes


def plan_edits(messages, changes):
    edits = []
    for message in messages:
        # Continuation batches have embeds but no header. Ended cards don't
        # have a "Termina" description, and must never be rewritten as starts.
        if message.get("content", "") not in ("", "**Novos eventos**"):
            continue
        embeds = json.loads(json.dumps(message.get("embeds", [])))
        touched = []
        for index, embed in enumerate(embeds):
            for change in changes:
                row = change["row"]
                if embed.get("title", "").partition(" ")[2] != row["name"]:
                    continue
                if not embed.get("description", "").startswith("Termina"):
                    continue
                if row.get("link") and row["link"] != embed.get("url"):
                    continue
                start = datetime.fromisoformat(change["start"])
                end = datetime.fromisoformat(change["end"])
                embed["description"] = (
                    f"Início: {start.day:02d} {PT_MONTHS_SHORT[start.month].lower()} às {start:%H:%M}\n"
                    f"Termina a {end.day:02d} {PT_MONTHS_SHORT[end.month].lower()} às {end:%H:%M}\n"
                    "Horários de Lisboa (corrigidos)."
                )
                touched.append(index)
        if touched:
            edits.append(
                {
                    "id": message["id"],
                    "before": message,
                    "embeds": embeds,
                    "indices": touched,
                }
            )
    return edits


def save_journal(path, journal):
    # Atomic replacement; each run gets a unique filename, including no-op runs.
    temporary = path.with_suffix(".tmp")
    temporary.touch(mode=0o600)
    temporary.write_text(json.dumps(journal, default=str, ensure_ascii=False, indent=2))
    temporary.replace(path)


async def main():
    resume = "--resume" in sys.argv
    apply = "--apply" in sys.argv or resume
    zone = ZoneInfo("Europe/Lisbon")
    today = datetime.now(zone).date()
    conn = None
    if resume:
        backup = Path(sys.argv[sys.argv.index("--resume") + 1])
        journal = json.loads(backup.read_text())
        if journal.get("database_status") != "committed":
            raise ValueError("Journal does not confirm a committed database repair")
        if journal["channel"] != Config.CONVIVIO_CHANNEL_ID:
            raise ValueError("Journal belongs to another channel")
    else:
        conn = connect(Config.DB_POLISWAG, dict_rows=True)
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM event WHERE end >= %s", (today,))
            rows = cur.fetchall()
        journal = {
            "channel": Config.CONVIVIO_CHANNEL_ID,
            "changes": plan_changes(rows),
            "edits": [],
            "database_status": "pending",
        }
        backup = Path(
            "/app/logs/event-timezone-backup-"
            + datetime.now().strftime("%Y%m%d%H%M%S")
            + "-"
            + uuid4().hex[:8]
            + ".json"
        )

    try:
        headers = {"Authorization": "Bot " + Config.DISCORD_API_KEY}
        base = "https://discord.com/api/v10"
        async with aiohttp.ClientSession(
            headers=headers, timeout=aiohttp.ClientTimeout(total=30)
        ) as session:

            async def request(method, path, **kwargs):
                for attempt in range(3):
                    async with session.request(
                        method, base + path, **kwargs
                    ) as response:
                        if response.status == 429 and attempt < 2:
                            delay = float((await response.json()).get("retry_after", 1))
                            if 0 <= delay <= 30:
                                await asyncio.sleep(delay)
                                continue
                        response.raise_for_status()
                        return await response.json()

            me = await request("GET", "/users/@me")
            channel = Config.CONVIVIO_CHANNEL_ID
            if not resume:
                midnight = datetime.combine(today, time.min, zone)
                after = (int(midnight.timestamp() * 1000) - 1420070400000) << 22
                messages, before = [], None
                while True:
                    params = {"limit": 100}
                    if before:
                        params["before"] = before
                    batch = await request(
                        "GET", f"/channels/{channel}/messages", params=params
                    )
                    messages.extend(
                        m
                        for m in batch
                        if int(m["id"]) > after and m["author"]["id"] == me["id"]
                    )
                    if not batch or min(int(m["id"]) for m in batch) <= after:
                        break
                    before = batch[-1]["id"]
                journal["edits"] = plan_edits(messages, journal["changes"])
            print(
                json.dumps(
                    {
                        "changes": len(journal["changes"]),
                        "edits": len(journal["edits"]),
                        "apply": apply,
                    }
                )
            )
            if not apply:
                return
            if not resume:
                save_journal(backup, journal)
                try:
                    with conn.cursor() as cur:
                        for change in journal["changes"]:
                            row = change["row"]
                            cur.execute(
                                "UPDATE event SET start=%s, end=%s WHERE name=%s AND start=%s AND end=%s",
                                (
                                    change["start"],
                                    change["end"],
                                    row["name"],
                                    row["start"],
                                    row["end"],
                                ),
                            )
                            if cur.rowcount != 1:
                                raise ValueError(
                                    f'Event changed during repair: {row["name"]}'
                                )
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
                journal["database_status"] = "committed"
                save_journal(backup, journal)
            print(f"Recovery journal: {backup}")
            for edit in journal["edits"]:
                if edit.get("status") in ("done", "deleted"):
                    continue
                path = f'/channels/{channel}/messages/{edit["id"]}'
                try:
                    current = await request("GET", path)
                except aiohttp.ClientResponseError as exc:
                    if exc.status != 404:
                        raise
                    edit["status"] = "deleted"
                    save_journal(backup, journal)
                    continue
                if current["author"]["id"] != me["id"]:
                    raise ValueError("Refusing to edit another author")
                # Do not overwrite independent edits between preview and apply.
                embeds = current.get("embeds", [])
                changed = False
                for index in edit["indices"]:
                    original, desired = (
                        edit["before"]["embeds"][index],
                        edit["embeds"][index],
                    )
                    if index >= len(embeds) or embeds[index].get(
                        "title"
                    ) != original.get("title"):
                        raise ValueError("Message structure changed; review journal")
                    if embeds[index].get("description") == desired["description"]:
                        continue
                    if embeds[index].get("description") != original.get("description"):
                        raise ValueError("Message description changed; review journal")
                    embeds[index]["description"] = desired["description"]
                    changed = True
                if changed:
                    result = await request(
                        "PATCH",
                        path,
                        json={"embeds": embeds, "allowed_mentions": {"parse": []}},
                    )
                    if [e.get("description") for e in result["embeds"]] != [
                        e.get("description") for e in embeds
                    ]:
                        raise ValueError("Discord edit verification failed")
                edit["status"] = "done"
                save_journal(backup, journal)
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    asyncio.run(main())
