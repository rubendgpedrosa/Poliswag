"""Game watcher (owner, 2026-11-23): every minute the Helpshift Known Issues
section and its newest article (modules.known_issues); every 5 minutes
pokemongo.com news and new commits in holoholo-text and pogo_assets, and every 10 the
Play Store version (modules.game_sources). Everything goes to the owner's
DM, or to KNOWN_ISSUES_CHANNEL_ID when it is set."""

import aiohttp
import discord
from discord.ext import commands, tasks

from modules import game_sources, known_issues
from modules.config import Config
from modules.http_client import get_session
from modules.owner_alert import notify_owner
from modules.state_store import StateFile

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PoGoLeiria-KnownIssues/1.0)"}


class KnownIssues(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.state = StateFile("known-issues")
        self.sources_state = StateFile("game-sources")
        self.failures = 0

    async def cog_load(self):
        if Config.IS_PRODUCTION:
            self.watch.start()
            self.sources.start()

    async def cog_unload(self):
        self.watch.cancel()
        self.sources.cancel()

    async def _get(self, url):
        async with get_session().get(
            url, headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=20)
        ) as response:
            response.raise_for_status()
            return await response.text()

    @tasks.loop(minutes=1)
    async def watch(self):
        # After repeated failures (blocked, down) check less often.
        if self.failures >= 5 and self.watch.current_loop % 10:
            return
        try:
            ids = known_issues.parse_section(await self._get(known_issues.SECTION_URL))
            if not ids:
                raise ValueError("empty section")
            newest_id, newest_url = ids[-1]
            article = known_issues.parse_article(await self._get(newest_url))
        except Exception as error:
            self.failures += 1
            if self.failures in (5, 50):
                self.poliswag.utility.log_to_file(
                    f"Known issues watcher failing: {type(error).__name__}", "ERROR"
                )
            return
        self.failures = 0
        urls = dict(ids)
        events, state = known_issues.decide(
            self.state.load(), [i for i, _ in ids], newest_id, article
        )
        for kind, value in events:
            if kind == "new":
                page = article if value == newest_id else None
                if page is None:
                    try:
                        page = known_issues.parse_article(await self._get(urls[value]))
                    except Exception:
                        page = None
                await self.post(
                    "Novo known issue",
                    page,
                    urls[value],
                )
            else:
                await self.post(
                    "Known issue atualizado", article, newest_url, old=value
                )
        self.state.save(state)

    async def post(self, kind, article, url, old=None):
        title = article["title"] if article else "Artigo novo"
        lines = [f"**{title}**", url]
        if article and article["status"]:
            lines.append(f"Estado: **{article['status']}**")
        if old is not None and article:
            change = known_issues.diff(old, article["body"])
            if change:
                lines.append(f"```diff\n{change}\n```")
        elif article:
            lines.append(article["body"][:1200])
        await self.send(kind, "\n".join(lines))

    @watch.before_loop
    async def before_watch(self):
        await self.poliswag.wait_until_ready()

    @tasks.loop(minutes=5)
    async def sources(self):
        state = self.sources_state.load()
        posts = []
        try:
            news = game_sources.parse_news(await self._get(game_sources.NEWS_URL))
            if news:
                fresh, state["news"] = game_sources.unseen(state.get("news"), news)
                for slug in fresh[:5]:
                    url = game_sources.NEWS_BASE + slug
                    try:
                        title = game_sources.parse_title(await self._get(url))
                    except Exception:
                        title = None
                    posts.append(
                        ("Notícia nova do Pokémon GO", f"**{title or slug}**\n{url}")
                    )
        except Exception:
            pass
        for repo, key, label in game_sources.REPOS:
            try:
                async with get_session().get(
                    game_sources.commits_api(repo),
                    headers={"Accept": "application/vnd.github+json", **_HEADERS},
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as response:
                    response.raise_for_status()
                    commits = game_sources.parse_commits(await response.json())
                if not commits:
                    continue
                messages = dict(commits)
                fresh, state[key] = game_sources.unseen(
                    state.get(key), [sha for sha, _ in commits]
                )
                for sha in fresh[:5]:
                    posts.append(
                        (
                            label,
                            f"**{messages.get(sha) or sha[:8]}**\n"
                            f"{game_sources.commit_url(repo, sha)}",
                        )
                    )
            except Exception:
                pass
        if self.sources.current_loop % 2 == 0:
            try:
                version = game_sources.parse_play_version(
                    await self._get(game_sources.PLAY_URL)
                )
                if version and version != state.get("play"):
                    if state.get("play"):
                        text = f"**{version}** (era {state['play']})"
                        working = getattr(
                            getattr(self.poliswag, "device_manager", None),
                            "working_game_version",
                            None,
                        )
                        try:
                            current = working() if callable(working) else None
                        except Exception:
                            current = None
                        if current:
                            text += f"\nO scanner corre com {current}."
                        posts.append(("Nova versão do Pokémon GO na Play Store", text))
                    state["play"] = version
            except Exception:
                pass
        for title, text in posts:
            await self.send(title, text)
        self.sources_state.save(state)

    @sources.before_loop
    async def before_sources(self):
        await self.poliswag.wait_until_ready()

    async def send(self, title, text):
        channel = (
            self.poliswag.get_channel(Config.KNOWN_ISSUES_CHANNEL_ID)
            if Config.KNOWN_ISSUES_CHANNEL_ID
            else None
        )
        if channel is not None:
            try:
                await channel.send(
                    embed=discord.Embed(
                        title=title, description=text[:3900], color=Config.EMBED_COLOR
                    ),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            except discord.HTTPException:
                pass
        await notify_owner(self.poliswag, text[:3900], title=title, tag="game-watch")


async def setup(poliswag):
    await poliswag.add_cog(KnownIssues(poliswag))
