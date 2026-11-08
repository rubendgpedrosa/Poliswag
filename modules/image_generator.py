import asyncio
import base64
import contextlib
import dataclasses
import io
import os
import tempfile

import aiohttp
import imgkit
from PIL import Image
from jinja2 import Environment, FileSystemLoader

from modules.account_card import build_card_view
from modules import stat_card
from modules.config import Config
from modules.http_client import get_session
from modules import scan_areas
from modules.quest_map import draw_quest_map, fit_quest_map

STAT_CARD_TEMPLATE = "stat-card.html"
# The site's brand artwork (apps/landing/public/brand-header-400.webp), as PNG.
BRAND_ART = "brand-header-400.png"
# Banner images are scaled to twice the panel width (the 2x render).
_BANNER_WIDTH = stat_card.PANEL_W * stat_card.RENDER_SCALE

# What imgkit.from_string prepends to every source it pipes; kept so rendering
# from a file cannot change how a template without its own charset tag renders.
_CHARSET_META = '<meta charset="UTF-8">'


def _recompress_png(data):
    """Losslessly re-encode a wkhtmltoimage PNG; the original bytes on failure.

    wkhtmltoimage writes barely compressed PNGs: the 2x account card comes out
    at ~5.8 MB and is re-uploaded every minute. Pillow's optimizer brings the
    same pixels to ~90 KB.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            out = io.BytesIO()
            image.save(out, "PNG", optimize=True)
        return out.getvalue()
    except Exception:
        return data


def _banner_data_uri(data):
    with Image.open(io.BytesIO(data)) as image:
        image = image.convert("RGB")
        if image.width > _BANNER_WIDTH:
            height = round(image.height * _BANNER_WIDTH / image.width)
            image = image.resize((_BANNER_WIDTH, height), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


class ImageGenerator:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.QUEST_MAP_URL = Config.QUEST_MAP_URL.rstrip("/")
        self.QUEST_MAP_STYLE = Config.QUEST_MAP_STYLE
        self.TEMPLATE_HTML_DIR = Config.TEMPLATE_HTML_DIR
        self.ACCOUNTS_TEMPLATE_HTML_FILE = Config.ACCOUNTS_TEMPLATE_HTML_FILE
        # Lazily-cached Jinja environment/templates — loaded and parsed from
        # disk once instead of on every render call (generate_image_from_
        # account_stats runs every 60s tick).
        self._env = None
        self._accounts_template = None
        self._stat_card_template = None
        self._brand_uri = None

    def _get_env(self):
        if self._env is None:
            self._env = Environment(loader=FileSystemLoader(self.TEMPLATE_HTML_DIR))
        return self._env

    async def _render_png(self, html_content, options, error_label):
        # Renders from a file we own rather than imgkit.from_string, which
        # pipes through stdin -- and wkhtmltoimage 0.12.6 spools stdin into
        # /tmp/wktemp-<uuid>.html and never deletes it, even on a clean exit.
        # That leaked one file per render, ~1440 a day on the 60s account tick.
        # The charset tag is the one imgkit prepends to every string source, so
        # wkhtmltoimage still receives byte-identical input.
        handle, path = tempfile.mkstemp(suffix=".html", prefix="poliswag-render-")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as page:
                page.write(_CHARSET_META + html_content)
            return await asyncio.to_thread(imgkit.from_file, path, False, options)
        except Exception as e:
            self.poliswag.utility.log_to_file(
                f"Error generating {error_label}: {e}", "ERROR"
            )
            return None
        finally:
            with contextlib.suppress(OSError):
                os.unlink(path)

    async def generate_image_from_account_stats(self, pools, area_performance=None):
        """Render the account card for `pools` (AccountMonitor.get_pool_stats)."""
        if self._accounts_template is None:
            self._accounts_template = self._get_env().get_template(
                self.ACCOUNTS_TEMPLATE_HTML_FILE
            )
        view = build_card_view(pools, area_performance)
        html_content = self._accounts_template.render(**view)
        options = {
            "format": "png",
            "encoding": "UTF-8",
            # CSS is laid out at view["width"] x view["height"] and scaled by
            # the template's CSS `zoom` (this wkhtmltoimage build ignores its
            # --zoom flag), so the PNG is scale x larger and text stays sharp
            # when Discord scales it down on phones. The height follows the
            # layout -- build_card_view sums the same pixel constants the
            # template's CSS uses.
            "width": str(view["width"] * view["scale"]),
            "height": str(view["height"] * view["scale"]),
            "quality": "100",
            "transparent": "",
            "javascript-delay": "1000",
            "quiet": "",
        }
        png = await self._render_png(html_content, options, "account image")
        if not png:
            return png
        return await asyncio.to_thread(_recompress_png, png)

    async def _banner(self, url):
        """The event's game image as a data: URI, scaled to the banner, or "".

        Inlined rather than left for wkhtmltoimage to fetch: a slow CDN then
        can't leave a blank banner, and the PNG doesn't depend on its timing.
        """
        if not url or not url.startswith("http"):
            return ""
        try:
            async with get_session().get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                response.raise_for_status()
                data = await response.read()
            return await asyncio.to_thread(_banner_data_uri, data)
        except Exception as error:
            self.poliswag.utility.log_to_file(
                f"Stat board banner {url} unavailable: {error}", "WARNING"
            )
            return ""

    def _brand(self):
        if self._brand_uri is None:
            path = os.path.join(self.TEMPLATE_HTML_DIR or "", BRAND_ART)
            try:
                with open(path, "rb") as art:
                    self._brand_uri = (
                        "data:image/png;base64," + base64.b64encode(art.read()).decode()
                    )
            except OSError:
                self._brand_uri = ""
        return self._brand_uri

    async def generate_stat_board(self, cards, heading, date=""):
        """PNG bytes of one stat board (modules.stat_card), or None."""
        if self._stat_card_template is None:
            self._stat_card_template = self._get_env().get_template(STAT_CARD_TEMPLATE)
        banners = await asyncio.gather(*(self._banner(card.image) for card in cards))
        cards = [
            dataclasses.replace(card, image=banner)
            for card, banner in zip(cards, banners)
        ]
        view = stat_card.build_board_view(cards, heading, date, self._brand())
        html_content = self._stat_card_template.render(**view)
        # Same sizing as the account card: CSS at the token sizes, CSS zoom
        # to the scaled PNG.
        options = {
            "format": "png",
            "encoding": "UTF-8",
            "width": str(view["width"] * view["scale"]),
            "height": str(view["height"] * view["scale"]),
            "quality": "100",
            "transparent": "",
            "javascript-delay": "1000",
            "quiet": "",
        }
        png = await self._render_png(html_content, options, "stat board")
        if not png:
            return png
        return await asyncio.to_thread(_recompress_png, png)

    async def generate_static_map_for_group_of_quests(self, pokestops, area=None):
        quest_map = fit_quest_map(pokestops, self.QUEST_MAP_STYLE)
        if quest_map is None:
            return None
        try:
            async with get_session().post(
                f"{self.QUEST_MAP_URL}/staticmap",
                json=quest_map.payload,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                response.raise_for_status()
                image_bytes = await response.read()
            return await asyncio.to_thread(
                draw_quest_map,
                image_bytes,
                quest_map,
                scan_areas.color_for(area),
            )
        except Exception as error:
            self.poliswag.utility.log_to_file(
                f"Error generating quest map: {error}", "ERROR"
            )
            # A map outage must still let the user receive the quest list.
            return None
