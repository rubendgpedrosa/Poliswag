"""Tests for modules.image_generator.ImageGenerator.

Focuses on the async quest-map renderer, the error branches of the async
HTML→PNG helpers (by making imgkit.from_file raise), and what every render
must keep: local fonts only, one options dict, escaped values.

The renderer writes the page to a temp file it deletes, so a test that wants
the rendered HTML reads the path it was handed.
"""

import datetime as dt
import os
import re
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from PIL import Image, ImageFont

from modules import stat_card, week_board
from modules.config import Config
from modules.image_generator import ImageGenerator, _recompress_png


class TestInit:
    def test_reads_config_and_starts_with_no_cache(self, mocker):
        mocker.patch.object(Config, "QUEST_MAP_URL", "http://tiles/")
        mocker.patch.object(Config, "QUEST_MAP_STYLE", "osm-bright")
        mocker.patch.object(Config, "TEMPLATE_HTML_DIR", "/templates")
        mocker.patch.object(Config, "ACCOUNTS_TEMPLATE_HTML_FILE", "accounts.html")

        g = ImageGenerator(poliswag=MagicMock())

        assert g.QUEST_MAP_URL == "http://tiles"
        assert g.QUEST_MAP_STYLE == "osm-bright"
        assert g.TEMPLATE_HTML_DIR == "/templates"
        assert g.ACCOUNTS_TEMPLATE_HTML_FILE == "accounts.html"
        assert g._env is None
        assert g._accounts_template is None
        assert g._font_css is None


def rendered(path):
    """What the template produced, without the charset tag the renderer prepends."""
    return open(path, encoding="utf-8").read().removeprefix('<meta charset="UTF-8">')


@pytest.fixture
def ig():
    g = ImageGenerator.__new__(ImageGenerator)
    g.poliswag = MagicMock()
    g.QUEST_MAP_URL = "http://tiles"
    g.QUEST_MAP_STYLE = "osm-bright"
    g.TEMPLATE_HTML_DIR = "/tmp"
    g.ACCOUNTS_TEMPLATE_HTML_FILE = "accounts.html"
    g._env = None
    g._accounts_template = None
    g._stat_card_template = None
    g._brand_uri = None
    g._font_css = None
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
            [{"lat": "39.7", "lon": "-8.8"}], area="marinha"
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
            # The area-coloured letter beside the stop's post (quest_map._LETTER_AT).
            letter = image.getpixel((574, 305))
            assert all(abs(a - b) <= 2 for a, b in zip(letter, (46, 204, 113)))

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

    async def test_map_never_downloads_reward_art(self, ig, mocker):
        # The reward is in the embed; the map only marks where each stop is.
        data = BytesIO()
        Image.new("RGB", (1200, 600), "white").save(data, format="PNG")
        session, _ = self.response(mocker, data.getvalue())
        result = await ig.generate_static_map_for_group_of_quests(
            [{"lat": 39.7, "lon": -8.8, "quest_slug": "pokemon/928.png"}]
        )
        assert result is not None
        session.get.assert_not_called()


def _summary(in_use=0, good=0, never_used=0, cooldown=0, out_detail=None):
    out_detail = out_detail or {}
    out = sum(out_detail.values())
    ready = good + never_used
    return {
        "in_use": in_use,
        "good": good,
        "never_used": never_used,
        "ready": ready,
        "cooldown": cooldown,
        "out": out,
        "out_detail": out_detail,
        "missing_token": 0,
        "total": in_use + ready + cooldown + out,
        "partition_sum": in_use + ready + cooldown + out,
    }


def _device(names=("PocoF2Pro",), connected=True, total=50, in_use=45):
    return {
        "connected": connected,
        "names": list(names) if connected else [],
        "workers": {
            "total": total if connected else 0,
            "in_use": in_use if connected else 0,
        },
    }


def _pools(level=True):
    pools = [
        {
            "key": "scanning",
            "title": "Scanning",
            "accounts": _summary(45, 199, 6, 57, {"banned": 5}),
            "device": _device(),
        }
    ]
    if level:
        pools.append(
            {
                "key": "levelling",
                "title": "Levelling",
                "accounts": _summary(16, 0, 0, 1),
                "device": _device(("RedmiNote4",), total=16, in_use=16),
            }
        )
    return pools


class TestGenerateImageFromAccountStats:
    async def test_returns_none_and_logs_on_error(self, ig, mocker, tmp_path):
        ig.TEMPLATE_HTML_DIR = str(tmp_path)
        (tmp_path / "accounts.html").write_text("<html>{{ grand_total }}</html>")
        mocker.patch(
            "modules.image_generator.imgkit.from_file",
            side_effect=RuntimeError("render failed"),
        )
        result = await ig.generate_image_from_account_stats(_pools())
        assert result is None
        ig.poliswag.utility.log_to_file.assert_called_once()
        msg, level = ig.poliswag.utility.log_to_file.call_args.args
        assert "account image" in msg
        assert level == "ERROR"

    async def test_image_height_follows_the_layout(self, ig, mocker, tmp_path):
        ig.TEMPLATE_HTML_DIR = str(tmp_path)
        (tmp_path / "accounts.html").write_text("<html>{{ height }}</html>")
        heights = []

        def fake_from_file(path, out, options):
            heights.append((int(options["height"]), int(rendered(path)[6:-7])))
            return b"IMG"

        mocker.patch(
            "modules.image_generator.imgkit.from_file", side_effect=fake_from_file
        )
        area = {"Leiria": {"total": 100, "iv": 99}}
        await ig.generate_image_from_account_stats(_pools(level=True), area)
        await ig.generate_image_from_account_stats(_pools(level=False), area)
        await ig.generate_image_from_account_stats(_pools(level=False), None)
        both, scan_only, no_footer = heights
        # The output is rendered at 2x; the template lays out at 1x.
        assert all(opt == 2 * tpl for opt, tpl in heights)
        assert both[0] > scan_only[0] > no_footer[0]

    async def test_real_template_renders_every_section(self, ig, mocker):
        ig.TEMPLATE_HTML_DIR = Config.TEMPLATE_HTML_DIR
        captured = {}
        mocker.patch(
            "modules.image_generator.imgkit.from_file",
            side_effect=lambda path, out, options: captured.setdefault(
                "html", rendered(path)
            )
            or b"IMG",
        )
        await ig.generate_image_from_account_stats(
            _pools(), {"Leiria": {"total": 100, "iv": 99}}
        )
        html = captured["html"]
        assert "Scanning" in html and "Levelling" in html
        assert "PocoF2Pro" in html and "RedmiNote4" in html
        assert "45/50 workers" in html and "16/16 workers" in html
        assert (
            '<span class="figure">329</span><span class="unit">in total</span>' in html
        )
        assert "ready includes 6 never used" in html and "out: 5 banned" in html
        assert 'class="status status-success"' in html and "Healthy" in html
        assert "status-bar" not in html
        assert "spawns/h" in html

    async def test_renders_at_twice_the_css_size(self, ig, mocker, tmp_path):
        ig.TEMPLATE_HTML_DIR = str(tmp_path)
        (tmp_path / "accounts.html").write_text("<html>{{ width }}</html>")
        seen = {}

        def fake_from_file(path, out, options):
            seen.update(options)
            return b"IMG"

        mocker.patch(
            "modules.image_generator.imgkit.from_file", side_effect=fake_from_file
        )
        await ig.generate_image_from_account_stats(_pools(level=False))
        assert seen["width"] == "1600"

    async def test_real_template_without_levelling_pool(self, ig, mocker):
        ig.TEMPLATE_HTML_DIR = Config.TEMPLATE_HTML_DIR
        captured = {}
        mocker.patch(
            "modules.image_generator.imgkit.from_file",
            side_effect=lambda path, out, options: captured.setdefault(
                "html", rendered(path)
            )
            or b"IMG",
        )
        await ig.generate_image_from_account_stats(_pools(level=False))
        html = captured["html"]
        assert "Scanning" in html and "Levelling" not in html
        assert "spawns/h" not in html

    async def _render_area_lines(self, ig, mocker, tmp_path, area_performance):
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
        await ig.generate_image_from_account_stats(_pools(), area_performance)
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
        assert html == "Leiria:100:99 Marinha Grande:40:90 "

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
        (tmp_path / "accounts.html").write_text("<html>{{ grand_total }}-v1</html>")
        captured = []

        def fake_from_file(path, out, options):
            captured.append(rendered(path))
            return b"IMG"

        mocker.patch(
            "modules.image_generator.imgkit.from_file", side_effect=fake_from_file
        )

        await ig.generate_image_from_account_stats(_pools(level=False))

        # Rewrite the template on disk before the second call — if the
        # template were being reloaded per call, this render would pick up
        # "v2". It should not: the cached Template object from the first
        # call is reused.
        (tmp_path / "accounts.html").write_text("<html>{{ grand_total }}-v2</html>")
        await ig.generate_image_from_account_stats(_pools(level=True))

        assert captured == ["<html>312-v1</html>", "<html>329-v1</html>"]


class TestRecompressPng:
    def test_shrinks_and_keeps_pixels(self):
        image = Image.new("RGBA", (64, 32), (12, 19, 29, 255))
        raw = BytesIO()
        image.save(raw, "PNG", compress_level=0)
        out = _recompress_png(raw.getvalue())
        assert len(out) < len(raw.getvalue())
        with Image.open(BytesIO(out)) as again:
            assert again.tobytes() == image.tobytes()

    def test_non_png_bytes_pass_through(self):
        assert _recompress_png(b"IMG") == b"IMG"


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


class TestDataUri:
    """Banners and Pokémon icons are inlined for the stat board."""

    @staticmethod
    def _decode(uri):
        import base64

        return Image.open(BytesIO(base64.b64decode(uri.split(",", 1)[1])))

    def test_a_small_sprite_is_cropped_and_scaled_up_to_its_square(self):
        from modules.image_generator import _data_uri

        icon = Image.new("RGBA", (40, 40), (255, 255, 255, 0))  # coloured but clear
        icon.paste((200, 30, 30, 255), (15, 10, 25, 30))  # a 10x20 sprite
        out = BytesIO()
        icon.save(out, "PNG")
        image = self._decode(_data_uri(out.getvalue(), square=100))
        assert image.size == (100, 100)
        # Height-bound: 20px tall becomes 100, so 10px wide becomes 50.
        box = image.getchannel("A").getbbox()
        assert (box[3] - box[1], box[2] - box[0]) == (100, 50)

    def test_a_banner_becomes_a_scaled_jpeg(self):
        from modules.image_generator import _data_uri

        out = BytesIO()
        Image.new("RGB", (2000, 1000), (10, 20, 30)).save(out, "JPEG")
        uri = _data_uri(out.getvalue(), width=1040)
        assert uri.startswith("data:image/jpeg;base64,")
        assert self._decode(uri).size == (1040, 520)


TEMPLATES = Path(__file__).resolve().parents[2] / "templates"


def capture_render(mocker):
    """Patch the renderer; the dict gets the last page's HTML and options."""
    seen = {}

    def fake_from_file(path, out, options):
        seen["html"] = rendered(path)
        seen["options"] = options
        return b"IMG"

    mocker.patch("modules.image_generator.imgkit.from_file", side_effect=fake_from_file)
    return seen


class TestFontFaces:
    """The fonts come from disk: a Google Fonts stall used to fail the card."""

    def test_five_local_faces_with_their_weights(self, ig):
        ig.TEMPLATE_HTML_DIR = str(TEMPLATES)
        css = ig._font_faces()
        faces = re.findall(
            r"font-family: '([^']+)';[^}]*font-weight: (\d+);[^}]*url\('([^']+)'\)",
            css,
        )
        assert [(family, int(weight)) for family, weight, _url in faces] == [
            ("Figtree", 600),
            ("Figtree", 700),
            ("Figtree", 800),
            ("Figtree", 900),
            ("Bricolage Grotesque", 800),
        ]
        assert css.count("@font-face") == 5
        assert "http" not in css
        for _family, _weight, url in faces:
            assert url.startswith("file:///")
            assert Path(url.removeprefix("file://")).is_file()

    def test_built_once(self, ig):
        ig.TEMPLATE_HTML_DIR = str(TEMPLATES)
        assert ig._font_faces() is ig._font_faces()

    @pytest.mark.parametrize(
        "name",
        ["figtree-600", "figtree-700", "figtree-800", "figtree-900", "bricolage-800"],
    )
    def test_each_font_is_a_static_instance(self, name):
        # wkhtmltoimage's Qt can't pick variable-font axes.
        font = ImageFont.truetype(str(TEMPLATES / "fonts" / f"{name}.ttf"))
        with pytest.raises(OSError):
            font.get_variation_axes()


class TestPngOptions:
    def test_one_options_dict_for_every_render(self, ig, tmp_path):
        ig.TEMPLATE_HTML_DIR = str(tmp_path)
        assert ig._png_options({"width": 400, "height": 300, "scale": 2}) == {
            "format": "png",
            "encoding": "UTF-8",
            "width": "800",
            "height": "600",
            "quality": "100",
            "transparent": "",
            "allow": str(tmp_path / "fonts"),
            "quiet": "",
        }

    def test_the_allowed_folder_holds_the_fonts(self, ig):
        ig.TEMPLATE_HTML_DIR = Config.TEMPLATE_HTML_DIR
        allowed = Path(ig._png_options({"width": 1, "height": 1, "scale": 1})["allow"])
        assert allowed.is_absolute()
        assert (allowed / "figtree-700.ttf").is_file()

    async def test_every_board_renders_with_it(self, ig, mocker):
        ig.TEMPLATE_HTML_DIR = Config.TEMPLATE_HTML_DIR
        seen = capture_render(mocker)
        options = mocker.spy(ig, "_png_options")
        card = stat_card.StatCard(kicker="k", hero=None, title="t", ended="e")
        event = week_board.WeekEvent(
            "Raid Hour", "Evento", "", NOW, NOW + dt.timedelta(hours=1)
        )
        renders = [
            ig.generate_image_from_account_stats(_pools()),
            ig.generate_stat_board([card], "Semana"),
            ig.generate_week_board([event], NOW),
        ]
        for render in renders:
            await render
            assert seen["options"] is options.spy_return
            assert "@font-face" in seen["html"]
        assert options.call_count == 3


NOW = dt.datetime(2026, 11, 9, 9, 0)
HOSTILE = "<b>x</b> & 'y'"
ESCAPED = "&lt;b&gt;x&lt;/b&gt; &amp; &#39;y&#39;"


class TestAutoescape:
    """Event names and bonuses come from ScrapedDuck: they are text, not markup."""

    async def test_stat_board_escapes_values(self, ig, mocker):
        ig.TEMPLATE_HTML_DIR = Config.TEMPLATE_HTML_DIR
        seen = capture_render(mocker)
        card = stat_card.StatCard(
            kicker=HOSTILE,
            hero=None,
            title=HOSTILE,
            chips=(("Horário", HOSTILE),),
            bonuses=(HOSTILE,),
        )
        await ig.generate_stat_board([card], HOSTILE)
        html = seen["html"]
        assert ESCAPED in html
        assert "<b>x</b>" not in html
        # Our own CSS still arrives as CSS: the font faces and the include.
        assert "@font-face { font-family: 'Figtree';" in html
        assert "font-family: 'Bricolage Grotesque', 'Figtree', sans-serif;" in html

    async def test_week_board_escapes_values(self, ig, mocker):
        ig.TEMPLATE_HTML_DIR = Config.TEMPLATE_HTML_DIR
        seen = capture_render(mocker)
        event = week_board.WeekEvent(
            HOSTILE, HOSTILE, "", NOW, NOW + dt.timedelta(hours=1)
        )
        await ig.generate_week_board([event], NOW)
        html = seen["html"]
        assert ESCAPED in html
        assert "<b>x</b>" not in html
        assert "@font-face { font-family: 'Figtree';" in html


class TestTemplatesNeedNoNetwork:
    """wkhtmltoimage fetches whatever a page references; a stall fails the
    render and holds up the scheduler tick. Nothing may come from the network."""

    NETWORK = re.compile(r"https?://|@import|url\(\s*['\"]?//", re.IGNORECASE)

    @pytest.mark.parametrize(
        "path",
        sorted(TEMPLATES.glob("*.html")) + sorted(TEMPLATES.glob("*.css")),
        ids=lambda path: path.name,
    )
    def test_no_network_reference(self, path):
        hits = [
            f"{path.name}:{number}: {line.strip()}"
            for number, line in enumerate(path.read_text().splitlines(), 1)
            if self.NETWORK.search(line)
        ]
        assert hits == []
