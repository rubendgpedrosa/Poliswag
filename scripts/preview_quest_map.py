#!/usr/bin/env python3
"""Render a quest reply from the latest PWA export; optionally DM the owner.

Run inside the bot container, which mounts /pogo-public/quests.json:
    python scripts/preview_quest_map.py --output /tmp/quest-preview --send-owner
"""

# ruff: noqa: E402 -- Add the repository path before importing its modules.

import argparse
import asyncio
from io import BytesIO
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import discord

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.config import Config
from modules.http_client import close_session
from modules.image_generator import ImageGenerator
from modules.quest_search import QuestSearch


async def preview(args):
    data = json.loads(Path(Config.QUEST_JSON_OUTPUT).read_text())
    is_leiria = args.area == "leiria"
    quests = [q for q in data["quests"] if q["reward"]["type"].startswith("encounter_")]
    quest = max(
        quests,
        key=lambda q: sum(s["zone"] == args.area for s in q["pokestops"]),
    )
    pokemon_id = quest["reward"]["type"].split("_")[1]
    stops = [
        {
            "name": stop["name"],
            "lat": stop["location"]["lat"],
            "lon": stop["location"]["lng"],
            "quest_slug": f"pokemon/{pokemon_id}.png",
        }
        for stop in quest["pokestops"]
        if stop["zone"] == args.area
    ]
    if not stops:
        raise RuntimeError(f"No encounter quests in {args.area}")
    # Only pure grouping/embed methods are needed; avoid DB/translation loading.
    search = QuestSearch.__new__(QuestSearch)
    search.UI_ICONS_URL = Config.UI_ICONS_URL
    pages = search.group_pokestops_geographically(stops, 10)
    reward_name = quest["reward"]["label"].removesuffix(" encounter")
    embed = search.create_quest_embed(
        f"{quest['title']} — {reward_name}",
        pages[0],
        is_leiria,
        1,
        len(pages),
        total_stops=len(stops),
    )
    bot = SimpleNamespace(
        utility=SimpleNamespace(
            log_to_file=lambda message, level: print(level, message)
        )
    )
    try:
        image = await ImageGenerator(bot).generate_static_map_for_group_of_quests(
            pages[0], is_leiria=is_leiria
        )
    finally:
        await close_session()
    if image is None:
        raise RuntimeError("Quest map could not be rendered")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "quest-map.png").write_bytes(image)
    embed.set_image(url="attachment://quest-map.png")
    (args.output / "embed.json").write_text(
        json.dumps(embed.to_dict(), indent=2, ensure_ascii=False)
    )
    (args.output / "stops.json").write_text(
        json.dumps(pages[0], indent=2, ensure_ascii=False)
    )
    print(f"Rendered {len(pages[0])} stops to {args.output / 'quest-map.png'}")
    if args.send_owner:
        # REST login only: no gateway connection, cogs, scheduler or notifications.
        async with discord.Client(intents=discord.Intents.none()) as client:
            await client.login(Config.DISCORD_API_KEY)
            owner = await client.fetch_user(Config.MY_ID)
            file = discord.File(BytesIO(image), filename="quest-map.png")
            try:
                message = await owner.send(
                    content="Pré-visualização do novo mapa de quests:",
                    embed=embed,
                    file=file,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            finally:
                file.close()
            (args.output / "discord-message.txt").write_text(message.jump_url + "\n")
            print(f"Sent owner preview: {message.jump_url}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area", choices=["leiria", "marinha"], default="leiria")
    parser.add_argument("--output", type=Path, default=Path("/tmp/quest-preview"))
    parser.add_argument("--send-owner", action="store_true")
    asyncio.run(preview(parser.parse_args()))


if __name__ == "__main__":
    main()
