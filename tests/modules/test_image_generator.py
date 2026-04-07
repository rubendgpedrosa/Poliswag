"""Tests for modules.image_generator.ImageGenerator.

Focuses on the pure-logic static-map URL builder and the error branches of
the async HTML→PNG helpers (by making imgkit.from_string raise).
"""

from unittest.mock import MagicMock

import pytest

from modules.image_generator import ImageGenerator


@pytest.fixture
def ig():
    g = ImageGenerator.__new__(ImageGenerator)
    g.poliswag = MagicMock()
    g.google_api_key = "KEY"
    g.TEMPLATE_HTML_DIR = "/tmp"
    g.FOLLOWED_EVENTS_TEMPLATE_HTML_FILE = "quests.html"
    g.ACCOUNTS_TEMPLATE_HTML_FILE = "accounts.html"
    g.QUEST_ICON_BASE_URL = "https://icons/"
    return g


class TestGenerateStaticMapForGroupOfQuests:
    def test_returns_none_for_empty(self, ig):
        assert ig.generate_static_map_for_group_of_quests([]) is None

    def test_returns_none_when_no_coords(self, ig):
        # None of the stops have lat/lon → coordinates list stays empty.
        stops = [{"name": "A"}, {"name": "B"}]
        assert ig.generate_static_map_for_group_of_quests(stops) is None

    def test_builds_url_with_markers(self, ig):
        stops = [
            {"lat": "39.7", "lon": "-8.8", "quest_slug": "reward/item/1.png"},
            {"lat": "39.75", "lon": "-8.85", "quest_slug": "pokemon/25.png"},
        ]
        url = ig.generate_static_map_for_group_of_quests(stops)
        assert url.startswith("https://maps.googleapis.com/maps/api/staticmap?")
        assert "key=KEY" in url
        assert "size=600x300" in url
        # Labels follow A, B, C… sequence.
        assert "label:A" in url
        assert "label:B" in url
        # Icon URLs are embedded.
        assert "icon:https://icons/reward/item/1.png" in url
        assert "icon:https://icons/pokemon/25.png" in url
        # Coordinates appear verbatim.
        assert "39.7,-8.8" in url
        assert "39.75,-8.85" in url

    def test_skips_stops_missing_one_axis(self, ig):
        stops = [
            {"lat": "39.7", "quest_slug": "a.png"},  # missing lon → skipped
            {"lat": "39.8", "lon": "-8.8", "quest_slug": "b.png"},
        ]
        url = ig.generate_static_map_for_group_of_quests(stops)
        assert "b.png" in url
        assert "a.png" not in url


class TestGenerateImageFromQuestData:
    async def test_returns_none_and_logs_on_error(self, ig, mocker, tmp_path):
        # Prepare a real template file so Jinja can render.
        ig.TEMPLATE_HTML_DIR = str(tmp_path)
        (tmp_path / "quests.html").write_text("<html>{{ has_leiria }}</html>")
        mocker.patch(
            "modules.image_generator.imgkit.from_string",
            side_effect=OSError("wkhtmltopdf missing"),
        )
        result = await ig.generate_image_from_quest_data([], [], True, False)
        assert result is None
        ig.poliswag.utility.log_to_file.assert_called_once()
        msg, level = ig.poliswag.utility.log_to_file.call_args.args
        assert "quest image" in msg
        assert level == "ERROR"

    async def test_returns_bytes_on_success(self, ig, mocker, tmp_path):
        ig.TEMPLATE_HTML_DIR = str(tmp_path)
        (tmp_path / "quests.html").write_text("<html>ok</html>")
        mocker.patch(
            "modules.image_generator.imgkit.from_string",
            return_value=b"PNGDATA",
        )
        result = await ig.generate_image_from_quest_data([], [], False, False)
        assert result == b"PNGDATA"


class TestGenerateImageFromAccountStats:
    async def test_returns_none_and_logs_on_error(self, ig, mocker, tmp_path):
        ig.TEMPLATE_HTML_DIR = str(tmp_path)
        (tmp_path / "accounts.html").write_text("<html>{{ good }}</html>")
        mocker.patch(
            "modules.image_generator.imgkit.from_string",
            side_effect=RuntimeError("render failed"),
        )
        result = await ig.generate_image_from_account_stats(
            {"good": 1, "cooldown": 2, "disabled": 3}, True
        )
        assert result is None
        ig.poliswag.utility.log_to_file.assert_called_once()
        msg, level = ig.poliswag.utility.log_to_file.call_args.args
        assert "account image" in msg
        assert level == "ERROR"

    async def test_returns_bytes_on_success_with_missing_keys_defaulted(
        self, ig, mocker, tmp_path
    ):
        ig.TEMPLATE_HTML_DIR = str(tmp_path)
        (tmp_path / "accounts.html").write_text("<html>{{ good }}</html>")
        captured = {}

        def fake_from_string(html, out, options):
            captured["html"] = html
            return b"IMG"

        mocker.patch(
            "modules.image_generator.imgkit.from_string", side_effect=fake_from_string
        )
        # Empty dict — .get() defaults all fields to 0.
        result = await ig.generate_image_from_account_stats({}, False)
        assert result == b"IMG"
        assert "0" in captured["html"]
