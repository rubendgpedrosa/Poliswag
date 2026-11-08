"""The scan-area registry, its fences and point-in-polygon classification."""

import json
from pathlib import Path

from unittest.mock import MagicMock

import pytest

from modules import scan_areas

GEO = Path(__file__).parent.parent / "fixtures" / "geo"


def geojson(name):
    return json.loads((GEO / f"{name}.geojson").read_text())


def square(lat_min, lon_min, lat_max, lon_max):
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon_min, lat_min],
                [lon_max, lat_min],
                [lon_max, lat_max],
                [lon_min, lat_max],
                [lon_min, lat_min],
            ]
        ],
    }


def row(key, sort_order, *, active=1, label=None, poracle=None, role=None):
    return {
        "area_key": key,
        "label": label or key.title(),
        "dragonite_area": key,
        "poracle_area": poracle or key,
        "alert_role": role or f"Alertas{key.title()}",
        "sort_order": sort_order,
        "active": active,
    }


# Real town centres, in small synthetic fences (the live Dragonite fences are
# available from the fixtures for the other three areas).
LEIRIA_POINT = (39.7436, -8.8071)
MARINHA_POINT = (39.7574, -8.9319)
MOITA_POINT = (39.70, -8.945)

TWO_AREAS = [row("leiria", 1), row("marinha", 2)]
TWO_FENCES = {
    "leiria": square(39.72, -8.87, 39.80, -8.75),
    "marinha": square(39.72, -8.99, 39.80, -8.92),
}
MOITA_ROW = row("moita", 5)
# The real Moita fence does not reach either synthetic town square.
MOITA_FENCE = geojson("Moita")


@pytest.fixture(autouse=True)
def _own_snapshot():
    """Start from an empty snapshot; the conftest seed is not wanted here."""
    scan_areas.reset()
    yield
    scan_areas.reset()


# ---------------------------------------------------------------------------
# Geometry


def test_point_inside_and_outside_a_polygon():
    moita = scan_areas.normalize_geometry(MOITA_FENCE)
    assert scan_areas.contains(moita, *MOITA_POINT)
    assert not scan_areas.contains(moita, 39.74, -8.80)


def test_hole_is_not_part_of_the_polygon():
    batalha = scan_areas.normalize_geometry(geojson("Batalha"))
    # Centroid of the outer ring: inside the outline, outside the hole.
    assert scan_areas.contains(batalha, 39.6584548, -8.8256477)
    # Centroid of the hole ring: inside the outline but excluded.
    assert not scan_areas.contains(batalha, 39.6595469, -8.8326741)


def test_multipolygon_matches_any_part():
    geometry = {
        "type": "MultiPolygon",
        "coordinates": [
            square(39.60, -8.90, 39.61, -8.89)["coordinates"],
            square(39.70, -8.80, 39.71, -8.79)["coordinates"],
        ],
    }
    polygons = scan_areas.normalize_geometry(geometry)
    assert scan_areas.contains(polygons, 39.605, -8.895)
    assert scan_areas.contains(polygons, 39.705, -8.795)
    assert not scan_areas.contains(polygons, 39.66, -8.85)


def test_geojson_feature_strings_and_plain_coordinates_are_accepted():
    feature = json.dumps(geojson("PortoDeMos"))
    assert scan_areas.normalize_geometry(feature)
    assert scan_areas.normalize_geometry(square(39.0, -9.0, 39.1, -8.9))
    assert scan_areas.normalize_geometry(
        {"path": [[39.0, -9.0], [39.1, -9.0], [39.1, -8.9]]}
    )
    assert scan_areas.normalize_geometry(
        {"multipath": [[[39.0, -9.0], [39.1, -9.0], [39.1, -8.9]]]}
    )


# ---------------------------------------------------------------------------
# Classification


def test_moita_coordinates_fall_in_moita_when_active():
    scan_areas.install(
        [*TWO_AREAS, MOITA_ROW],
        {**TWO_FENCES, "moita": MOITA_FENCE},
    )
    assert scan_areas.area_for(*MOITA_POINT) == "moita"


def test_moita_coordinates_fall_in_no_area_when_inactive():
    # The point is west of Leiria but nowhere near Marinha's real fence: it is
    # skipped, never guessed into the neighbouring area.
    scan_areas.install(TWO_AREAS, TWO_FENCES)
    assert scan_areas.area_for(*MOITA_POINT) is None


def test_real_leiria_and_marinha_points_are_classified():
    scan_areas.install(TWO_AREAS, TWO_FENCES)
    assert scan_areas.area_for(*LEIRIA_POINT) == "leiria"
    assert scan_areas.area_for(*MARINHA_POINT) == "marinha"
    assert scan_areas.area_for(38.0, -9.5) is None


def test_batalha_and_porto_de_mos_fixtures_classify_their_points():
    rows = [row("batalha", 3), row("portodemos", 4)]
    scan_areas.install(
        rows,
        {
            "batalha": geojson("Batalha"),
            "portodemos": geojson("PortoDeMos"),
        },
    )
    assert scan_areas.area_for(39.6330728, -8.8239954) == "batalha"
    assert scan_areas.area_for(39.5815922, -8.825309) == "portodemos"
    # The Batalha hole belongs to no area at all.
    assert scan_areas.area_for(39.6595469, -8.8326741) is None


def test_overlapping_fences_resolve_by_sort_order():
    overlap = square(39.70, -8.87, 39.80, -8.85)
    scan_areas.install(
        [row("leiria", 1), row("moita", 5)],
        {"leiria": overlap, "moita": overlap},
    )
    assert scan_areas.area_for(39.75, -8.86) == "leiria"


def test_inactive_rows_are_ignored_everywhere():
    scan_areas.install(
        [row("leiria", 1, active=0), row("marinha", 2)],
        {"leiria": TWO_FENCES["leiria"], "marinha": TWO_FENCES["marinha"]},
    )
    assert scan_areas.active_keys() == ["marinha"]
    assert scan_areas.area_for(*LEIRIA_POINT) is None
    assert scan_areas.label("leiria") == "Leiria"


# ---------------------------------------------------------------------------
# Registry-derived helpers


def test_area_labels_and_alert_roles_come_from_the_registry():
    scan_areas.install(
        [
            row("leiria", 1, label="Leiria", role="AlertasLeiria"),
            row("marinha", 2, active=0),
            row("batalha", 3, active=0, label="Batalha", role="AlertasBatalha"),
            row("portodemos", 4, active=0),
            row("moita", 5, active=0),
        ],
        {},
    )
    assert scan_areas.label("leiria") == "Leiria"
    assert scan_areas.label("nope") == "nope"
    assert scan_areas.short_label("leiria") == "Leiria"
    assert scan_areas.short_label("marinha") == "Marinha"
    assert scan_areas.short_label("nope") == "nope"
    assert scan_areas.labels() == "Leiria"
    assert scan_areas.alert_roles() == ["AlertasLeiria"]
    assert scan_areas.bit_for("leiria") == 1
    assert scan_areas.bit_for("batalha") == 4
    assert scan_areas.bit_for("portodemos") == 8
    assert scan_areas.bit_for("moita") == 16
    assert scan_areas.setting_code(["moita", "leiria"]) == 17
    assert scan_areas.color_for("marinha") == scan_areas.AREA_COLORS[1]


def test_bbox_is_the_union_of_the_active_fences():
    scan_areas.install(TWO_AREAS, TWO_FENCES)
    assert scan_areas.bbox() == (39.72, -8.99, 39.80, -8.75)
    assert scan_areas.area_bbox("leiria") == (39.72, -8.87, 39.80, -8.75)
    assert scan_areas.area_bbox("moita") is None


# ---------------------------------------------------------------------------
# Loading: registry, Dragonite API and the on-disk cache


def _rows():
    return [*TWO_AREAS, MOITA_ROW]


async def test_refresh_reads_the_registry_and_dragonite_then_caches(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(scan_areas, "_read_registry", _rows)
    monkeypatch.setattr(
        scan_areas.Config, "SCAN_AREA_CACHE", str(tmp_path / "areas.json")
    )

    async def fetch(rows):
        return {
            "leiria": TWO_FENCES["leiria"],
            "marinha": TWO_FENCES["marinha"],
            "moita": MOITA_FENCE,
        }

    monkeypatch.setattr(scan_areas, "_fetch_fences", fetch)
    assert await scan_areas.refresh() is True
    assert scan_areas.area_for(*MOITA_POINT) == "moita"

    payload = json.loads((tmp_path / "areas.json").read_text())
    assert set(payload["areas"]) == {"leiria", "marinha", "moita"}
    assert payload["areas"]["moita"]["dragonite_area"] == "moita"


async def test_dragonite_failure_falls_back_to_the_disk_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(scan_areas, "_read_registry", _rows)
    cache = tmp_path / "areas.json"
    monkeypatch.setattr(scan_areas.Config, "SCAN_AREA_CACHE", str(cache))
    # A previous good fetch left the cache on disk.
    scan_areas._write_cache({"moita": MOITA_FENCE}, [MOITA_ROW])

    async def fail(rows):
        raise RuntimeError("Dragonite is restarting")

    monkeypatch.setattr(scan_areas, "_fetch_fences", fail)
    assert await scan_areas.refresh() is True
    assert scan_areas.area_for(*MOITA_POINT) == "moita"
    # Without a cached fence an active area is simply unknown, never guessed.
    assert scan_areas.area_for(*LEIRIA_POINT) is None


async def test_dragonite_failure_without_a_cache_keeps_classification_empty(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(scan_areas, "_read_registry", _rows)
    monkeypatch.setattr(scan_areas.Config, "SCAN_AREA_CACHE", str(tmp_path / "no.json"))

    async def fail(rows):
        raise RuntimeError("Dragonite is down")

    monkeypatch.setattr(scan_areas, "_fetch_fences", fail)
    await scan_areas.refresh()
    assert scan_areas.active_keys() == ["leiria", "marinha", "moita"]
    assert scan_areas.area_for(*MOITA_POINT) is None


async def test_registry_read_failure_keeps_the_previous_snapshot(monkeypatch):
    scan_areas.install(TWO_AREAS, TWO_FENCES)

    def fail():
        raise RuntimeError("db down")

    monkeypatch.setattr(scan_areas, "_read_registry", fail)
    assert await scan_areas.refresh() is False
    assert scan_areas.area_for(*LEIRIA_POINT) == "leiria"


async def test_dragonite_listing_and_detail_are_parsed(monkeypatch):
    leiria = {**row("leiria", 1), "dragonite_area": "Leiria"}
    marinha = {**row("marinha", 2), "dragonite_area": "MarinhaGrande"}
    rows = [leiria, marinha]

    async def fetch_json(session, url):
        if url.endswith("/v2/areas/"):
            return {
                "areas": [
                    {"id": 9, "name": "Leiria"},
                    {"id": 10, "name": "MarinhaGrande"},
                ]
            }
        if url.endswith("/v2/areas/9"):
            return {"id": 9, "name": "Leiria", "geometry": TWO_FENCES["leiria"]}
        return {"id": 10, "name": "MarinhaGrande", "geometry": TWO_FENCES["marinha"]}

    monkeypatch.setattr(scan_areas, "_fetch_json", fetch_json)
    monkeypatch.setattr(scan_areas, "_session", lambda: MagicMock())
    monkeypatch.setattr(
        scan_areas.Config, "DRAGONITE_API_URL", "http://dragonite.test:7272"
    )
    fences = await scan_areas._fetch_fences(rows)
    assert set(fences) == {"leiria", "marinha"}
    assert scan_areas.normalize_geometry(fences["leiria"])


async def test_ensure_refreshes_at_most_every_ten_minutes(monkeypatch, tmp_path):
    calls = []

    monkeypatch.setattr(scan_areas, "_read_registry", _rows)
    monkeypatch.setattr(
        scan_areas.Config, "SCAN_AREA_CACHE", str(tmp_path / "areas.json")
    )

    async def fetch(rows):
        calls.append(1)
        return {"moita": MOITA_FENCE}

    monkeypatch.setattr(scan_areas, "_fetch_fences", fetch)
    assert await scan_areas.ensure() is True
    assert await scan_areas.ensure() is True
    assert calls == [1]
    # Push the snapshot past its window: the next ensure refreshes again.
    scan_areas._loaded_at -= scan_areas.REFRESH_SECONDS + 1
    scan_areas._last_attempt -= scan_areas.REFRESH_SECONDS + 1
    assert await scan_areas.ensure() is True
    assert calls == [1, 1]
