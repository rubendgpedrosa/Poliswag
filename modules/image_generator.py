import asyncio
import contextlib
from collections import OrderedDict
import os
import tempfile

import aiohttp
import imgkit
from jinja2 import Environment, FileSystemLoader

from modules.config import Config
from modules.http_client import get_session
from modules.quest_map import draw_quest_map, fit_quest_map

# Display order + short label for the account card's area-performance
# footer -- fixed order so Leiria always renders before Marinha
# regardless of dict/query row order.
_AREA_LABELS = (("Leiria", "Leiria"), ("MarinhaGrande", "Marinha"))

# What imgkit.from_string prepends to every source it pipes; kept so rendering
# from a file cannot change how a template without its own charset tag renders.
_CHARSET_META = '<meta charset="UTF-8">'


class ImageGenerator:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.QUEST_MAP_URL = Config.QUEST_MAP_URL.rstrip("/")
        self.QUEST_MAP_STYLE = Config.QUEST_MAP_STYLE
        self.QUEST_ICON_BASE_URL = Config.UI_ICONS_URL
        self._quest_marker_icons = OrderedDict()
        self.TEMPLATE_HTML_DIR = Config.TEMPLATE_HTML_DIR
        self.ACCOUNTS_TEMPLATE_HTML_FILE = Config.ACCOUNTS_TEMPLATE_HTML_FILE
        # Lazily-cached Jinja environment/templates — loaded and parsed from
        # disk once instead of on every render call (generate_image_from_
        # account_stats runs every 60s tick).
        self._env = None
        self._accounts_template = None

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

    async def generate_image_from_account_stats(
        self, account_data, device_status, area_performance=None, workers=None
    ):
        if self._accounts_template is None:
            self._accounts_template = self._get_env().get_template(
                self.ACCOUNTS_TEMPLATE_HTML_FILE
            )
        area_lines = []
        for db_area, label in _AREA_LABELS:
            stats = (area_performance or {}).get(db_area)
            if not stats or not stats.get("total"):
                continue
            rate = round(stats.get("iv", 0) / stats["total"] * 100)
            area_lines.append({"name": label, "rate": rate, "spawns": stats["total"]})
        html_content = self._accounts_template.render(
            good=account_data.get("good", 0),
            cooldown=account_data.get("cooldown", 0),
            disabled=account_data.get("disabled", 0),
            device_status=device_status,
            area_lines=area_lines,
            # Falsy/zero totals render nothing at all in the template --
            # only RotomNG reports worker figures, so a legacy Rotom
            # payload hides the chip instead of claiming "0 workers".
            workers=workers or {},
        )
        options = {
            "format": "png",
            "encoding": "UTF-8",
            "width": "800",
            # Taller than the original 220 to fit the per-area footer
            # without shrinking the headline numbers. Must stay in sync
            # with the html/body/.stage height in accounts.html.
            "height": "260",
            "quality": "100",
            "transparent": "",
            "javascript-delay": "1000",
            "quiet": "",
        }
        return await self._render_png(html_content, options, "account image")

    async def _load_quest_marker_icon(self, slug):
        if slug in self._quest_marker_icons:
            self._quest_marker_icons.move_to_end(slug)
            return self._quest_marker_icons[slug]
        if not self.QUEST_ICON_BASE_URL:
            return None
        try:
            async with get_session().get(
                f"{self.QUEST_ICON_BASE_URL.rstrip('/')}/{slug.lstrip('/')}",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as response:
                response.raise_for_status()
                data = await response.read()
            self._quest_marker_icons[slug] = data
            if len(self._quest_marker_icons) > 64:
                self._quest_marker_icons.popitem(last=False)
            return data
        except (aiohttp.ClientError, TimeoutError) as error:
            self.poliswag.utility.log_to_file(
                f"Quest marker icon unavailable ({slug}): {error}", "WARNING"
            )
            return None

    async def generate_static_map_for_group_of_quests(self, pokestops, is_leiria=True):
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
            slugs = list(dict.fromkeys(quest_map.icon_slugs.values()))
            data = await asyncio.gather(
                *(self._load_quest_marker_icon(slug) for slug in slugs)
            )
            icons = {slug: icon for slug, icon in zip(slugs, data) if icon}
            return await asyncio.to_thread(
                draw_quest_map, image_bytes, quest_map, is_leiria, icons
            )
        except Exception as error:
            self.poliswag.utility.log_to_file(
                f"Error generating quest map: {error}", "ERROR"
            )
            # A map outage must still let the user receive the quest list.
            return None
