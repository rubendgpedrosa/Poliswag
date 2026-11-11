"""Poracle geofence export from the scan-area registry."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts import sync_poracle_geofences as sync


@pytest.fixture(autouse=True)
def handle(monkeypatch):
    """No test here opens a real database connection."""
    connector = MagicMock()
    monkeypatch.setattr(sync, "DatabaseConnector", connector)
    return connector


def row(key, order, poracle=None, active=1):
    return {
        "area_key": key,
        "label": key.title(),
        "dragonite_area": key,
        "poracle_area": poracle or key,
        "alert_role": f"Alertas{key.title()}",
        "sort_order": order,
        "active": active,
    }


LEIRIA = {
    "type": "Polygon",
    "coordinates": [
        [
            [-8.90, 39.70],
            [-8.80, 39.70],
            [-8.80, 39.80],
            [-8.90, 39.80],
            [-8.90, 39.70],
        ]
    ],
}
MULTI = {
    "type": "MultiPolygon",
    "coordinates": [
        [
            [
                [-9.00, 39.70],
                [-8.95, 39.70],
                [-8.95, 39.75],
                [-9.00, 39.70],
            ]
        ],
        [
            [
                [-9.10, 39.70],
                [-9.05, 39.70],
                [-9.05, 39.75],
                [-9.10, 39.70],
            ]
        ],
    ],
}


def test_single_polygon_becomes_poracle_path_with_lat_lon_pairs():
    entries = sync.build_entries([row("leiria", 1)], {"leiria": LEIRIA})
    assert entries == [
        {
            "id": 1,
            "name": "leiria",
            "userSelectable": True,
            "displayInMatches": True,
            "group": "",
            "path": [
                [39.70, -8.90],
                [39.70, -8.80],
                [39.80, -8.80],
                [39.80, -8.90],
                [39.70, -8.90],
            ],
        }
    ]


def test_multipolygon_becomes_poracle_multipath():
    entries = sync.build_entries(
        [row("marinha", 2, "marinhagrande")], {"marinha": MULTI}
    )
    entry = entries[0]
    assert "path" not in entry
    assert entry["name"] == "marinhagrande"
    assert entry["id"] == 2
    assert len(entry["multipath"]) == 2
    assert entry["multipath"][0][0] == [39.70, -9.00]
    assert sync.point_count(entry) == 8


def test_holes_are_dropped_from_the_outline(capsys):
    holed = {
        "type": "Polygon",
        "coordinates": [
            LEIRIA["coordinates"][0],
            [[-8.85, 39.72], [-8.82, 39.72], [-8.82, 39.74], [-8.85, 39.72]],
        ],
    }
    entries = sync.build_entries([row("leiria", 1)], {"leiria": holed})
    assert len(entries[0]["path"]) == 5
    assert "buraco" in capsys.readouterr().err


def test_diff_reports_new_changed_unchanged_and_removed():
    entries = sync.build_entries(
        [row("leiria", 1), row("marinha", 2, "marinhagrande")],
        {"leiria": LEIRIA, "marinha": MULTI},
    )
    existing = [
        {"id": 1, "name": "leiria", "path": [[39.70, -8.90]]},  # same, fewer points
        {"id": 3, "name": "batalha", "path": [[39.60, -8.80]]},  # no longer active
    ]
    lines = sync.diff_lines(entries, existing)
    assert "  ~ leiria:" in lines[0]
    assert "  + marinhagrande:" in lines[1]
    assert "  - batalha:" in lines[2]


def test_write_keeps_the_engine_ids_and_round_trips(monkeypatch, tmp_path, capsys):
    rows = [row("leiria", 1), row("marinha", 2, "marinhagrande")]
    monkeypatch.setattr(sync.scan_areas, "refresh", AsyncMock(return_value=True))
    monkeypatch.setattr(sync.scan_areas, "active_areas", lambda: rows)
    monkeypatch.setattr(
        sync.scan_areas,
        "fences",
        lambda: {"leiria": LEIRIA, "marinha": MULTI},
    )
    target = tmp_path / "geofence.json"
    assert sync.main(["--write", str(target)]) == 0
    payload = json.loads(target.read_text())
    assert [entry["id"] for entry in payload] == [1, 2]
    assert [entry["name"] for entry in payload] == ["leiria", "marinhagrande"]
    assert payload[0]["userSelectable"] is True
    assert payload[0]["displayInMatches"] is True
    assert payload[0]["group"] == ""
    assert "multipath" in payload[1]
    assert "Escrito" in capsys.readouterr().out


def test_default_run_writes_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sync.scan_areas, "refresh", AsyncMock(return_value=True))
    monkeypatch.setattr(sync.scan_areas, "active_areas", lambda: [row("leiria", 1)])
    monkeypatch.setattr(sync.scan_areas, "fences", lambda: {"leiria": LEIRIA})
    assert sync.main([]) == 0
    out = capsys.readouterr().out
    assert "Nada escrito" in out and "1 área(s) ativa(s)" in out
    assert not (tmp_path / "geofence.json").exists()


def test_a_missing_fence_fails_before_writing(monkeypatch):
    monkeypatch.setattr(sync.scan_areas, "refresh", AsyncMock(return_value=True))
    monkeypatch.setattr(sync.scan_areas, "active_areas", lambda: [row("leiria", 1)])
    monkeypatch.setattr(sync.scan_areas, "fences", lambda: {})
    with pytest.raises(SystemExit, match="Sem geofence"):
        sync.main([])


def test_missing_fences_can_be_allowed(monkeypatch, capsys):
    monkeypatch.setattr(sync.scan_areas, "refresh", AsyncMock(return_value=True))
    monkeypatch.setattr(sync.scan_areas, "active_areas", lambda: [row("leiria", 1)])
    monkeypatch.setattr(sync.scan_areas, "fences", lambda: {})
    assert sync.main(["--allow-missing"]) == 0
    assert "0 área(s) ativa(s)" in capsys.readouterr().out


def test_the_registry_is_read_through_a_poliswag_handle(monkeypatch, handle):
    seen = []

    async def refresh():
        seen.append(sync.scan_areas._db)
        return True

    monkeypatch.setattr(sync.scan_areas, "refresh", refresh)
    monkeypatch.setattr(sync.scan_areas, "active_areas", lambda: [row("leiria", 1)])
    monkeypatch.setattr(sync.scan_areas, "fences", lambda: {"leiria": LEIRIA})
    assert sync.main([]) == 0
    handle.assert_called_once_with(sync.Config.DB_POLISWAG)
    assert seen == [handle.return_value]


def test_a_database_that_cannot_connect_fails_like_a_failed_read(monkeypatch, handle):
    handle.side_effect = RuntimeError("down")
    refresh = AsyncMock(return_value=True)
    monkeypatch.setattr(sync.scan_areas, "refresh", refresh)
    with pytest.raises(SystemExit, match="registo scan_area"):
        sync.main([])
    refresh.assert_not_awaited()
