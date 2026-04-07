"""Tests for modules.embeds builder helpers."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import discord

from modules import embeds


class TestBuildEmbed:
    def test_title_and_description(self):
        embed = embeds.build_embed("T", "D")
        assert isinstance(embed, discord.Embed)
        assert embed.title == "T"
        assert embed.description == "D"
        assert embed.footer.text is None

    def test_default_description_is_empty(self):
        embed = embeds.build_embed("Only Title")
        assert embed.description == ""

    def test_footer_is_set_when_provided(self):
        embed = embeds.build_embed("T", "D", footer="foot")
        assert embed.footer.text == "foot"

    def test_timestamp_is_set(self):
        before = datetime.now()
        embed = embeds.build_embed("T")
        after = datetime.now()
        ts = embed.timestamp.replace(tzinfo=None)
        assert before <= ts <= after


class TestBuildTrackedListEmbed:
    async def test_empty_list_shows_placeholder_description(self):
        db = MagicMock()
        db.get_data_from_database.return_value = []
        embed = await embeds.build_tracked_list_embed(db)
        assert "Não há quests" in embed.description
        assert embed.footer.text is None

    async def test_populates_fields_from_rows(self):
        db = MagicMock()
        db.get_data_from_database.return_value = [
            {
                "target": "Catch 5 Pokémon",
                "creator": "alice",
                "createddate": datetime(2024, 1, 2, 15, 4),
            },
            {
                "target": "Great Throws",
                "creator": "bob",
                "createddate": "ontem",
            },
        ]
        embed = await embeds.build_tracked_list_embed(db)
        assert len(embed.fields) == 2
        assert embed.fields[0].name == "Catch 5 Pokémon"
        assert "alice" in embed.fields[0].value
        assert "02/01/2024 15:04" in embed.fields[0].value
        assert embed.fields[1].name == "Great Throws"
        assert "bob" in embed.fields[1].value
        assert "ontem" in embed.fields[1].value

    async def test_truncates_field_name_to_256_chars(self):
        db = MagicMock()
        long_target = "x" * 500
        db.get_data_from_database.return_value = [
            {"target": long_target, "creator": "c", "createddate": "2024-01-01"}
        ]
        embed = await embeds.build_tracked_list_embed(db)
        assert len(embed.fields[0].name) == 256

    async def test_overflow_footer_shows_counts_and_returns_early(self):
        db = MagicMock()
        rows = [
            {"target": f"Q{i}", "creator": "c", "createddate": "2024-01-01"}
            for i in range(30)
        ]
        db.get_data_from_database.return_value = rows
        embed = await embeds.build_tracked_list_embed(
            db, footer_text="ignored because overflow wins"
        )
        assert len(embed.fields) == 25
        assert "25 de 30" in embed.footer.text
        # footer_text should NOT be applied — function returned before that branch.
        assert "ignored" not in embed.footer.text

    async def test_footer_text_applied_on_normal_path(self):
        db = MagicMock()
        db.get_data_from_database.return_value = [
            {"target": "t", "creator": "c", "createddate": "2024-01-01"}
        ]
        embed = await embeds.build_tracked_list_embed(db, footer_text="hello")
        assert embed.footer.text == "hello"

    async def test_custom_title(self):
        db = MagicMock()
        db.get_data_from_database.return_value = []
        embed = await embeds.build_tracked_list_embed(db, title="Custom")
        assert embed.title == "Custom"

    async def test_missing_createddate_falls_back(self):
        db = MagicMock()
        db.get_data_from_database.return_value = [
            {"target": "t", "creator": "c"}  # no createddate
        ]
        embed = await embeds.build_tracked_list_embed(db)
        assert "Data desconhecida" in embed.fields[0].value


class TestBuildExcludedListEmbed:
    async def test_empty_returns_placeholder(self):
        db = MagicMock()
        db.get_data_from_database.return_value = []
        embed = await embeds.build_excluded_list_embed(db)
        assert "Não há tipos" in embed.description

    async def test_populates_description(self):
        db = MagicMock()
        db.get_data_from_database.return_value = [
            {"type": "raid"},
            {"type": "invasion"},
        ]
        embed = await embeds.build_excluded_list_embed(db)
        assert "- raid" in embed.description
        assert "- invasion" in embed.description

    async def test_footer_applied(self):
        db = MagicMock()
        db.get_data_from_database.return_value = [{"type": "raid"}]
        embed = await embeds.build_excluded_list_embed(db, footer_text="foot")
        assert embed.footer.text == "foot"

    async def test_truncates_oversized_description(self):
        db = MagicMock()
        # Force the joined list well beyond 4096 chars.
        db.get_data_from_database.return_value = [
            {"type": "a" * 50} for _ in range(200)
        ]
        embed = await embeds.build_excluded_list_embed(db)
        assert "(truncado)" in embed.description


class TestBuildTrackedSummaryEmbeds:
    async def test_sends_one_embed_per_reward_group(self):
        channel = MagicMock()
        sent = []

        async def capture(*, embed):
            sent.append(embed)

        channel.send = capture

        reward_groups = {
            "reward/item/1.png": {
                "title": "Poké Balls",
                "reward_text": "3x Poké Ball",
                "pokestops": [{"name": "A"}, {"name": "B"}],
            },
            "pokemon/25.png": {
                "title": "Pikachu",
                "reward_text": "Pikachu",
                "pokestops": [{"name": "C"}],
            },
        }

        await embeds.build_tracked_summary_embeds(
            channel, reward_groups, "foot", "https://icons/"
        )

        assert len(sent) == 2
        # First embed: 2 pokestops → "2 quest de Poké Balls"
        assert "2 quest de Poké Balls" in sent[0].description
        assert sent[0].footer.text == "foot"
        assert sent[0].author.name == "3x Poké Ball"
        assert sent[0].author.icon_url == "https://icons/reward/item/1.png"

    async def test_missing_reward_text_defaults_to_empty(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        reward_groups = {
            "reward/stardust/0.png": {
                "title": "Stardust",
                "pokestops": [{"name": "A"}],
            }
        }
        await embeds.build_tracked_summary_embeds(
            channel, reward_groups, "foot", "https://icons/"
        )
        sent_embed = channel.send.call_args.kwargs["embed"]
        assert sent_embed.author.name == ""

    async def test_empty_reward_groups_sends_nothing(self):
        channel = MagicMock()
        channel.send = AsyncMock()
        await embeds.build_tracked_summary_embeds(channel, {}, "foot", "https://icons/")
        channel.send.assert_not_called()
