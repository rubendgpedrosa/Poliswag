"""The scan-area registry and the Dragonite fences behind it.

An area is a row in ``scan_area`` (``area_key``, label, Dragonite area name,
Poracle geofence name, alert role, display order, active). Nothing outside
this module names an individual area: adding one is a row plus its Dragonite
area, and every reader follows the registry. Rows with ``active = 0`` are
ignored everywhere.

The polygon of an area is its Dragonite area's geofence, fetched from
Dragonite's HTTP API (``/v2/areas/`` then ``/v2/areas/{id}``), refreshed every
ten minutes. The last good copy is kept on disk under the bot's data
directory so a Dragonite restart never empties the areas. A point inside no
active fence belongs to no area: it is skipped, never guessed into one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path

import aiohttp

from modules.config import Config

REFRESH_SECONDS = 600
# After a failed refresh, wait this long before hammering Dragonite again.
RETRY_SECONDS = 60
HTTP_TIMEOUT = 20
# Display colours by sort_order: the map markers and quest embeds follow the
# same order as the registry, so a new area gets one without touching code.
AREA_COLORS = ("#3498DB", "#2ECC71", "#E67E22", "#9B59B6", "#1ABC9C", "#E74C3C")

_REGISTRY_SQL = (
    "SELECT area_key, label, dragonite_area, poracle_area, alert_role, "
    "sort_order, active FROM scan_area ORDER BY sort_order, area_key"
)

logger = logging.getLogger(__name__)
_lock = asyncio.Lock()

# All registry rows (active and not) in sort order, and the normalized fences
# of the active ones: key -> ((ring, ...), ...).
_rows: list[dict] = []
_fences: dict[str, tuple] = {}
_loaded_at: float | None = None
_last_attempt: float | None = None
# The poliswag database handle the registry is read through (see use()).
_db = None


# ---------------------------------------------------------------------------
# Geometry


def _is_point(value) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return False
    try:
        float(value[0])
        float(value[1])
    except (TypeError, ValueError):
        return False
    return True


def _ring(points, *, lat_first: bool) -> tuple:
    ring = []
    for point in points:
        if not _is_point(point):
            continue
        first, second = float(point[0]), float(point[1])
        # GeoJSON stores [lon, lat]; Poracle's path/multipath is [lat, lon].
        ring.append((first, second) if lat_first else (second, first))
    if len(ring) >= 3 and ring[0] != ring[-1]:
        ring.append(ring[0])
    return tuple(ring)


def _polygon(rings, *, lat_first: bool) -> tuple:
    cleaned = []
    for entry in rings:
        if not isinstance(entry, (list, tuple)) or len(entry) < 3:
            continue
        ring = _ring(entry, lat_first=lat_first)
        if len(ring) >= 4:
            cleaned.append(ring)
    return tuple(cleaned)


def _single_polygon(rings, *, lat_first: bool) -> tuple:
    """One polygon: the first ring is the outline, the rest are holes."""
    if not isinstance(rings, (list, tuple)) or not rings:
        return ()
    if _is_point(rings[0]):
        rings = [rings]
    return _polygon(rings, lat_first=lat_first)


def normalize_geometry(value) -> tuple:
    """Normalize whatever Dragonite answers with to ``((ring, ...), ...)``.

    Accepts GeoJSON (Polygon/MultiPolygon, bare or wrapped in a Feature or
    FeatureCollection) and the ``path``/``multipath`` lists Poracle uses for
    geofences. Rings are ``(lat, lon)`` tuples; the first ring is the outline
    and the remaining ones are holes.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return ()
    if isinstance(value, dict):
        kind = str(value.get("type") or "").lower()
        if kind == "featurecollection":
            polygons = []
            for feature in value.get("features") or []:
                polygons.extend(normalize_geometry(feature))
            return tuple(polygons)
        if kind == "feature":
            return normalize_geometry(value.get("geometry"))
        if kind == "polygon":
            polygon = _single_polygon(value.get("coordinates") or [], lat_first=False)
            return (polygon,) if polygon else ()
        if kind == "multipolygon":
            polygons = []
            for rings in value.get("coordinates") or []:
                polygon = _single_polygon(rings, lat_first=False)
                if polygon:
                    polygons.append(polygon)
            return tuple(polygons)
        for key in ("geometry", "fence", "geofence", "boundary", "area", "polygon"):
            if value.get(key):
                polygons = normalize_geometry(value[key])
                if polygons:
                    return polygons
        if value.get("path"):
            polygon = _single_polygon([value["path"]], lat_first=True)
            return (polygon,) if polygon else ()
        if value.get("multipath"):
            polygons = []
            for path in value["multipath"] or []:
                polygon = _single_polygon([path], lat_first=True)
                if polygon:
                    polygons.append(polygon)
            return tuple(polygons)
        if value.get("coordinates"):
            polygon = _single_polygon(value["coordinates"], lat_first=False)
            return (polygon,) if polygon else ()
        return ()
    if isinstance(value, (list, tuple)):
        if not value:
            return ()
        if any(isinstance(item, dict) for item in value):
            polygons = []
            for item in value:
                polygons.extend(normalize_geometry(item))
            return tuple(polygons)
        polygon = _single_polygon(value, lat_first=False)
        return (polygon,) if polygon else ()
    return ()


def _in_ring(lat: float, lon: float, ring) -> bool:
    """Ray casting; a point on an edge is not guaranteed either way."""
    inside = False
    count = len(ring)
    j = count - 1
    for i in range(count):
        yi, xi = ring[i]
        yj, xj = ring[j]
        if (yi > lat) != (yj > lat):
            crossing = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < crossing:
                inside = not inside
        j = i
    return inside


def contains(polygons, lat, lon) -> bool:
    """True when the point is inside a polygon and outside all its holes."""
    for polygon in polygons or ():
        if not polygon:
            continue
        if not _in_ring(lat, lon, polygon[0]):
            continue
        if any(_in_ring(lat, lon, hole) for hole in polygon[1:]):
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# Registry snapshot


def _normalize_row(row) -> dict:
    return {
        "area_key": str(row.get("area_key") or ""),
        "label": str(row.get("label") or row.get("area_key") or ""),
        "dragonite_area": str(row.get("dragonite_area") or ""),
        "poracle_area": str(row.get("poracle_area") or ""),
        "alert_role": str(row.get("alert_role") or ""),
        "sort_order": int(row.get("sort_order") or 0),
        "active": int(row.get("active") or 0),
    }


def active_areas() -> list[dict]:
    """The active registry rows, in display order."""
    return [dict(row) for row in _rows if row["active"]]


def active_keys() -> list[str]:
    return [row["area_key"] for row in _rows if row["active"]]


def row_for(key) -> dict | None:
    for row in _rows:
        if row["area_key"] == key:
            return dict(row)
    return None


def label(key) -> str:
    row = row_for(key)
    return row["label"] if row else str(key)


def short_label(key) -> str:
    """Compact form for narrow footers: the first word of a multi-word label.

    "Marinha Grande" -> "Marinha" (the account card's original wording), while
    a one-word area keeps its whole name.
    """
    words = label(key).split()
    return words[0] if words else str(key)


def labels(separator=" · ") -> str:
    return separator.join(row["label"] for row in _rows if row["active"])


def alert_roles() -> list[str]:
    return [row["alert_role"] for row in _rows if row["active"] and row["alert_role"]]


def dragonite_area_names() -> list[str]:
    return [row["dragonite_area"] for row in _rows if row["active"]]


def bit_for(key) -> int:
    """The hundo_confirmed_setting bit: one per sort_order (1, 2, 4, 8, ...)."""
    row = row_for(key)
    if not row or row["sort_order"] <= 0:
        return 0
    return 1 << (row["sort_order"] - 1)


def color_for(key, fallback=AREA_COLORS[0]) -> str:
    """Display colour by sort_order, stable across a restart."""
    row = row_for(key)
    if not row or row["sort_order"] <= 0:
        return fallback
    return AREA_COLORS[(row["sort_order"] - 1) % len(AREA_COLORS)]


def setting_code(keys) -> int:
    """A set of area keys as the one number hundo_confirmed_setting stores."""
    return sum(bit_for(key) for key in keys or ())


# The two per-area quest commands kept from before the generic !quest form.
LEGACY_QUEST_COMMANDS = {"leiria": "questleiria", "marinha": "questmarinha"}


def fences() -> dict[str, tuple]:
    """Normalized fences of the active areas (a copy; safe to mutate)."""
    return dict(_fences)


def area_for(lat, lon) -> str | None:
    """The first active area whose fence holds the point, or None.

    Never guesses: outside every active fence there is no area. Overlapping
    fences resolve to the lower sort_order, so the result is deterministic.
    """
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None
    for row in _rows:
        if not row["active"]:
            continue
        if contains(_fences.get(row["area_key"]), lat, lon):
            return row["area_key"]
    return None


def area_bbox(key):
    """(lat_min, lon_min, lat_max, lon_max) of an area's fence, or None."""
    points = [
        point
        for polygon in _fences.get(key) or ()
        for ring in polygon
        for point in ring
    ]
    if not points:
        return None
    lats = [point[0] for point in points]
    lons = [point[1] for point in points]
    return min(lats), min(lons), max(lats), max(lons)


def bbox():
    """Union bbox of every active fence, or None without fences."""
    boxes = [box for box in (area_bbox(row["area_key"]) for row in _rows) if box]
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def install(rows, fences) -> None:
    """Replace the in-memory snapshot (tests, or a caller with its own rows)."""
    global _rows, _fences, _loaded_at, _last_attempt
    _rows = [_normalize_row(row) for row in rows or ()]
    _fences = {
        str(key): normalize_geometry(value) for key, value in (fences or {}).items()
    }
    _loaded_at = time.monotonic()
    _last_attempt = _loaded_at


def use(db) -> None:
    """Give the module the database handle the registry is read through.

    The bot calls it once in setup_hook with `poliswag.db`; scripts pass a
    handle of their own. Until then refresh() fails like a failed read.
    """
    global _db
    _db = db


def reset() -> None:
    """Forget the snapshot, its freshness clock and the database handle.

    Only tests call it; dropping the handle too keeps one test's fake
    database from leaking into the next.
    """
    global _rows, _fences, _loaded_at, _last_attempt, _db
    _db = None
    _rows = []
    _fences = {}
    _loaded_at = None
    _last_attempt = None


# ---------------------------------------------------------------------------
# Loading


def _read_registry(connection) -> list[dict]:
    with connection.cursor() as cursor:
        cursor.execute(_REGISTRY_SQL)
        return [_normalize_row(row) for row in cursor.fetchall()]


def _cache_path() -> Path:
    return Path(Config.SCAN_AREA_CACHE)


def _read_cache() -> dict[str, object]:
    """Raw geometries from the last good fetch, keyed by area_key."""
    try:
        payload = json.loads(_cache_path().read_text())
    except (OSError, ValueError, TypeError):
        return {}
    areas = payload.get("areas") if isinstance(payload, dict) else None
    if not isinstance(areas, dict):
        return {}
    return {
        str(key): value.get("geometry")
        for key, value in areas.items()
        if isinstance(value, dict) and value.get("geometry") is not None
    }


def _write_cache(geometries: dict[str, object], rows: list[dict]) -> None:
    """Atomically replace the cache; a failed write is not fatal."""
    names = {row["area_key"]: row["dragonite_area"] for row in rows}
    payload = {
        "fetched_at": int(time.time()),
        "areas": {
            key: {"dragonite_area": names.get(key, ""), "geometry": geometry}
            for key, geometry in geometries.items()
        },
    }
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except OSError as error:
        logger.warning("scan-area fence cache write failed: %s", error)


def _area_items(payload) -> list[dict]:
    if isinstance(payload, dict):
        for key in ("areas", "data", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        # A single area object.
        return [payload] if payload.get("name") else []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _area_id(items: list[dict], name: str):
    wanted = name.strip().casefold()
    for item in items:
        candidate = item.get("name") or item.get("area_name")
        if isinstance(candidate, str) and candidate.strip().casefold() == wanted:
            return item, item.get("id")
    return None, None


def _geometry_from(payload):
    if isinstance(payload, dict):
        for key in (
            "geometry",
            "fence",
            "geofence",
            "boundary",
            "area",
            "polygon",
        ):
            if payload.get(key):
                return payload[key]
        if payload.get("type") or payload.get("path") or payload.get("multipath"):
            return payload
    return None


async def _fetch_json(session, url: str):
    async with session.get(
        url, timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
    ) as response:
        response.raise_for_status()
        return await response.json(content_type=None)


async def _fetch_fences(rows: list[dict]) -> dict[str, object]:
    """Raw geofence payload per active area; raises when Dragonite fails."""
    base = Config.DRAGONITE_API_URL.rstrip("/")
    session = _session()
    listing = await _fetch_json(session, f"{base}/v2/areas/")
    items = _area_items(listing)
    geometries: dict[str, object] = {}
    for row in rows:
        item, area_id = _area_id(items, row["dragonite_area"])
        if item is None:
            raise LookupError(
                f"Dragonite area {row['dragonite_area']!r} is not in /v2/areas/"
            )
        detail = item
        if area_id is not None:
            detail = await _fetch_json(session, f"{base}/v2/areas/{area_id}")
        geometry = _geometry_from(detail) or _geometry_from(item)
        if not normalize_geometry(geometry):
            raise ValueError(
                f"Dragonite area {row['dragonite_area']!r} has no usable geofence"
            )
        geometries[row["area_key"]] = geometry
    return geometries


def _session():
    # Imported lazily so this module stays importable without aiohttp setup.
    from modules.http_client import get_session

    return get_session()


async def refresh(log=None) -> bool:
    """Reload the registry and its fences; keep the old snapshot on failure.

    Registry rows come from the database. Fences come from Dragonite, falling
    back to the last good on-disk copy (per area) when a request fails.
    """
    global _rows, _fences, _loaded_at, _last_attempt

    def _log(message, level="ERROR"):
        if log:
            log(message, level)
        else:
            logger.log(logging.ERROR if level == "ERROR" else logging.INFO, message)

    async with _lock:
        _last_attempt = time.monotonic()
        try:
            if _db is None:
                raise RuntimeError("no database handle (scan_areas.use was not called)")
            rows = await _db.unit(_read_registry)
        except Exception as error:
            _log(f"scan_area registry read failed: {error}", "ERROR")
            return False
        active = [row for row in rows if row["active"]]
        cached = _read_cache()
        geometries: dict[str, object] = {}
        fetched = True
        try:
            geometries = await _fetch_fences(active)
        except Exception as error:
            fetched = False
            _log(f"Dragonite area fences unavailable: {error}", "ERROR")
        for row in active:
            key = row["area_key"]
            if key in geometries:
                continue
            if key in cached:
                geometries[key] = cached[key]
                continue
            if fetched:
                _log(f"scan area {key!r} has no fence; it will not match", "ERROR")
        if fetched:
            _write_cache(geometries, active)
        _rows = rows
        _fences = {key: normalize_geometry(value) for key, value in geometries.items()}
        _loaded_at = time.monotonic()
        return True


async def ensure(log=None) -> bool:
    """Refresh when the snapshot is older than REFRESH_SECONDS."""
    now = time.monotonic()
    if _loaded_at is not None and now - _loaded_at < REFRESH_SECONDS:
        return True
    if _last_attempt is not None and now - _last_attempt < RETRY_SECONDS:
        return False
    return await refresh(log)
