"""Tests for modules.image_generator.ImageGenerator.

Focuses on the async quest-map renderer and the error branches of
the async HTML→PNG helpers (by making imgkit.from_file raise).

The renderer writes the page to a temp file it deletes, so a test that wants
the rendered HTML reads the path it was handed.
"""

import os
from collections import OrderedDict
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import aiohttp
from PIL import Image
import pytest

from modules.config import Config
from modules.image_generator import ImageGenerator


class TestInit:
    def test_reads_config_and_starts_with_no_cache(self, mocker):
        mocker.patch.object(Config, "QUEST_MAP_URL", "http://tiles/")
        mocker.patch.object(Config, "QUEST_MAP_STYLE", "osm-bright")
        mocker.patch.object(Config, "TEMPLATE_HTML_DIR", "/templates")
        mocker.patch.object(Config, "ACCOUNTS_TEMPLATE_HTML_FILE", "accounts.html")
        mocker.patch.object(Config, "UI_ICONS_URL", "https://icons/")

        g = ImageGenerator(poliswag=MagicMock())

        assert g.QUEST_MAP_URL == "http://tiles"
        assert g.QUEST_MAP_STYLE == "osm-bright"
        assert g.QUEST_ICON_BASE_URL == "https://icons/"
        assert not g._quest_marker_icons
        assert g.TEMPLATE_HTML_DIR == "/templates"
        assert g.ACCOUNTS_TEMPLATE_HTML_FILE == "accounts.html"
        assert g._env is None
        assert g._accounts_template is None


def rendered(path):
    """What the template produced, without the charset tag the renderer prepends."""
    return open(path, encoding="utf-8").read().removeprefix('<meta charset="UTF-8">')


@pytest.fixture
def ig():
    g = ImageGenerator.__new__(ImageGenerator)
    g.poliswag = MagicMock()
    g.QUEST_MAP_URL = "http://tiles"
    g.QUEST_MAP_STYLE = "osm-bright"
    g.QUEST_ICON_BASE_URL = "https://icons/"
    g._quest_marker_icons = OrderedDict()
    g.TEMPLATE_HTML_DIR = "/tmp"
    g.ACCOUNTS_TEMPLATE_HTML_FILE = "accounts.html"
    g._env = None
    g._accounts_template = None
    return g


class TestGenerateStaticMapForGroupOfQuests:
    async def test_returns_none_for_empty(self, ig):
        assert await ig.generate_static_map_for_group_of_quests([]) is None

    async def test_returns_none_when_no_coords(self, ig):
        # None of the stops have lat/lon → coordinates list stays empty.
        stops = [{"name": "A"}, {"name": "B"}]
        assert await ig.generate_static_map_for_group_of_quests(stops) is None

    def response(self, mocker, data):
        response = MagicMock()
        response.read = AsyncMock(return_value=data)
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post.return_value = context
        mocker.patch("modules.image_generator.get_session", return_value=session)
        return session, response

    async def test_returns_labelled_png_from_self_hosted_map(self, ig, mocker):
        data = BytesIO()
        Image.new("RGB", (1200, 600), "white").save(data, format="PNG")
        session, response = self.response(mocker, data.getvalue())
        result = await ig.generate_static_map_for_group_of_quests(
            [{"lat": "39.7", "lon": "-8.8"}], is_leiria=False
        )
        response.raise_for_status.assert_called_once()
        args, kwargs = session.post.call_args
        assert args == ("http://tiles/staticmap",)
        assert kwargs["json"]["style"] == "osm-bright"
        assert kwargs["json"]["scale"] == 2
        assert kwargs["timeout"].total == 20
        with Image.open(BytesIO(result)) as image:
            assert image.size == (1200, 600)
            assert image.format == "PNG"
            assert image.getpixel((610, 312)) == (46, 204, 113)

    async def test_http_failure_logs_and_returns_none(self, ig, mocker):
        _, response = self.response(mocker, b"")
        response.raise_for_status.side_effect = aiohttp.ClientError("unavailable")
        result = await ig.generate_static_map_for_group_of_quests(
            [{"lat": 39.7, "lon": -8.8}]
        )
        assert result is None
        assert "unavailable" in ig.poliswag.utility.log_to_file.call_args.args[0]
        assert ig.poliswag.utility.log_to_file.call_args.args[1] == "ERROR"

    async def test_bad_image_keeps_results_available(self, ig, mocker):
        self.response(mocker, b"not an image")
        assert (
            await ig.generate_static_map_for_group_of_quests(
                [{"lat": 39.7, "lon": -8.8}]
            )
            is None
        )
        ig.poliswag.utility.log_to_file.assert_called_once()

    async def test_reward_icon_is_requested_once_and_cached(self, ig, mocker):
        session, _ = self.response(mocker, b"map")
        icon_response = MagicMock()
        icon_response.read = AsyncMock(return_value=b"sprite")
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=icon_response)
        context.__aexit__ = AsyncMock(return_value=False)
        session.get.return_value = context

        assert await ig._load_quest_marker_icon("pokemon/928.png") == b"sprite"
        assert await ig._load_quest_marker_icon("pokemon/928.png") == b"sprite"
        session.get.assert_called_once()
        assert session.get.call_args.args == ("https://icons/pokemon/928.png",)

    async def test_icon_failure_still_returns_labelled_map(self, ig, mocker):
        data = BytesIO()
        Image.new("RGB", (1200, 600), "white").save(data, format="PNG")
        session, _ = self.response(mocker, data.getvalue())
        session.get.side_effect = aiohttp.ClientError("icon unavailable")

        result = await ig.generate_static_map_for_group_of_quests(
            [{"lat": 39.7, "lon": -8.8, "quest_slug": "pokemon/928.png"}]
        )
        assert result is not None
        with Image.open(BytesIO(result)) as image:
            assert image.size == (1200, 600)
        assert ig.poliswag.utility.log_to_file.call_args.args[1] == "WARNING"


class TestGenerateImageFromAccountStats:
    async def test_returns_none_and_logs_on_error(self, ig, mocker, tmp_path):
        ig.TEMPLATE_HTML_DIR = str(tmp_path)
        (tmp_path / "accounts.html").write_text("<html>{{ good }}</html>")
        mocker.patch(
            "modules.image_generator.imgkit.from_file",
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

        def fake_from_file(path, out, options):
            captured["html"] = rendered(path)
            return b"IMG"

        mocker.patch(
            "modules.image_generator.imgkit.from_file", side_effect=fake_from_file
        )
        # Empty dict — .get() defaults all fields to 0.
        result = await ig.generate_image_from_account_stats({}, False)
        assert result == b"IMG"
        assert "0" in captured["html"]

    async def _render_area_lines(self, ig, mocker, tmp_path, area_performance, **kw):
        ig.TEMPLATE_HTML_DIR = str(tmp_path)
        (tmp_path / "accounts.html").write_text(
            "{% for l in area_lines %}"
            "{{ l.name }}:{{ l.spawns }}:{{ l.rate }} "
            "{% endfor %}"
        )
        captured = {}
        mocker.patch(
            "modules.image_generator.imgkit.from_file",
            side_effect=lambda path, out, options: captured.setdefault(
                "html", rendered(path)
            )
            or b"IMG",
        )
        await ig.generate_image_from_account_stats(
            {"good": 1, "cooldown": 2, "disabled": 3},
            True,
            area_performance,
            **kw,
        )
        return captured["html"]

    async def test_area_performance_passes_spawns_and_rounded_rate(
        self, ig, mocker, tmp_path
    ):
        html = await self._render_area_lines(
            ig,
            mocker,
            tmp_path,
            {
                "Leiria": {"total": 100, "iv": 99},
                "MarinhaGrande": {"total": 40, "iv": 36},
            },
        )
        assert html == "Leiria:100:99 Marinha:40:90 "

    async def test_area_performance_leiria_always_before_marinha(
        self, ig, mocker, tmp_path
    ):
        # Dict insertion order is reversed here -- output order must not be.
        html = await self._render_area_lines(
            ig,
            mocker,
            tmp_path,
            {
                "MarinhaGrande": {"total": 10, "iv": 10},
                "Leiria": {"total": 10, "iv": 10},
            },
        )
        assert html.index("Leiria") < html.index("Marinha")

    async def test_area_with_zero_total_is_omitted(self, ig, mocker, tmp_path):
        html = await self._render_area_lines(
            ig,
            mocker,
            tmp_path,
            {
                "Leiria": {"total": 100, "iv": 99},
                "MarinhaGrande": {"total": 0, "iv": 0},
            },
        )
        assert "Leiria" in html
        assert "Marinha" not in html

    async def test_area_performance_none_renders_no_lines(self, ig, mocker, tmp_path):
        html = await self._render_area_lines(ig, mocker, tmp_path, None)
        assert html == ""

    async def test_area_performance_empty_dict_renders_no_lines(
        self, ig, mocker, tmp_path
    ):
        html = await self._render_area_lines(ig, mocker, tmp_path, {})
        assert html == ""

    async def test_template_is_loaded_once_and_reused(self, ig, mocker, tmp_path):
        ig.TEMPLATE_HTML_DIR = str(tmp_path)
        (tmp_path / "accounts.html").write_text("<html>{{ good }}-v1</html>")
        captured = []

        def fake_from_file(path, out, options):
            captured.append(rendered(path))
            return b"IMG"

        mocker.patch(
            "modules.image_generator.imgkit.from_file", side_effect=fake_from_file
        )

        await ig.generate_image_from_account_stats({"good": 1}, True)

        # Rewrite the template on disk before the second call — if the
        # template were being reloaded per call, this render would pick up
        # "v2". It should not: the cached Template object from the first
        # call is reused.
        (tmp_path / "accounts.html").write_text("<html>{{ good }}-v2</html>")
        await ig.generate_image_from_account_stats({"good": 2}, True)

        assert captured == ["<html>1-v1</html>", "<html>2-v1</html>"]


class TestRenderPngLeavesNothingBehind:
    """wkhtmltoimage 0.12.6 spools stdin into /tmp/wktemp-<uuid>.html and never
    deletes it, and imgkit.from_string always uses stdin — one leaked file per
    render, ~1440/day on the 60s account tick. Rendering from a file we own and
    delete is the fix; these tests hold that seam in place."""

    async def test_renders_from_a_file_it_owns_and_then_removes_it(self, ig, mocker):
        seen = {}

        def fake_from_file(path, out, options):
            seen["path"] = path
            seen["content"] = open(path, encoding="utf-8").read()
            return b"PNG"

        mocker.patch(
            "modules.image_generator.imgkit.from_file", side_effect=fake_from_file
        )
        result = await ig._render_png(
            "<html>olá</html>", {"format": "png"}, "test image"
        )

        assert result == b"PNG"
        assert seen["content"].endswith("<html>olá</html>")
        assert not os.path.exists(seen["path"])

    async def test_keeps_the_charset_prefix_the_string_path_used_to_add(
        self, ig, mocker
    ):
        seen = {}

        def fake_from_file(path, out, options):
            seen["content"] = open(path, encoding="utf-8").read()
            return b"PNG"

        mocker.patch(
            "modules.image_generator.imgkit.from_file", side_effect=fake_from_file
        )
        await ig._render_png("<html>x</html>", {"format": "png"}, "test image")

        # imgkit prepended this for every string source; a template without its
        # own charset tag would otherwise start rendering accents differently.
        assert seen["content"].startswith('<meta charset="UTF-8">')

    async def test_removes_the_file_even_when_the_render_blows_up(self, ig, mocker):
        paths = []

        def boom(path, out, options):
            paths.append(path)
            raise OSError("wkhtmltopdf missing")

        mocker.patch("modules.image_generator.imgkit.from_file", side_effect=boom)
        result = await ig._render_png("<html>x</html>", {"format": "png"}, "test image")

        assert result is None
        assert not os.path.exists(paths[0])

    async def test_never_touches_the_leaking_stdin_path(self, ig, mocker):
        from_string = mocker.patch("modules.image_generator.imgkit.from_string")
        mocker.patch("modules.image_generator.imgkit.from_file", return_value=b"PNG")
        await ig._render_png("<html>x</html>", {"format": "png"}, "test image")
        from_string.assert_not_called()
