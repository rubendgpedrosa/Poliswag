"""Tests for modules.mega_exporter.

_fetch_webp does real PokeAPI + PIL work; tests mock requests.get and feed
it a genuine tiny PNG (via PIL itself) so Image.open/save exercise real
decode/encode logic instead of a mocked PIL.
"""

import io
import json
from unittest.mock import MagicMock

import pytest
from PIL import Image

from modules.mega_exporter import MegaExporter, _fetch_webp, _key_to_pokeapi_slugs


class TestKeyToPokeapiSlugs:
    def test_primal_key(self):
        assert _key_to_pokeapi_slugs("primal-groudon", 383) == [
            "groudon-primal",
            "groudon",
            "383",
        ]

    def test_mega_x_suffix(self):
        assert _key_to_pokeapi_slugs("mega-charizard-x", 6) == [
            "charizard-mega-x",
            "charizard-mega",
            "charizard",
            "6",
        ]

    def test_mega_y_suffix(self):
        assert _key_to_pokeapi_slugs("mega-charizard-y", 6) == [
            "charizard-mega-y",
            "charizard-mega",
            "charizard",
            "6",
        ]

    def test_plain_mega(self):
        assert _key_to_pokeapi_slugs("mega-venusaur", 3) == [
            "venusaur-mega",
            "venusaur",
            "3",
        ]


def _png_bytes():
    """A tiny real PNG so PIL can genuinely decode/re-encode it."""
    img = Image.new("RGBA", (2, 2), (255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _artwork_response(url="http://img/art.png"):
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "sprites": {"other": {"official-artwork": {"front_default": url}}}
    }
    return resp


class TestFetchWebp:
    def test_saves_webp_on_first_successful_slug(self, tmp_path, mocker):
        dest = tmp_path / "mega-venusaur.webp"
        img_response = MagicMock()
        img_response.content = _png_bytes()
        get = mocker.patch(
            "modules.mega_exporter.requests.get",
            side_effect=[_artwork_response(), img_response],
        )
        assert _fetch_webp("mega-venusaur", 3, dest) is True
        assert dest.exists()
        assert get.call_count == 2

    def test_falls_back_to_next_slug_on_404(self, tmp_path, mocker):
        dest = tmp_path / "mega-charizard-x.webp"
        img_response = MagicMock()
        img_response.content = _png_bytes()
        mocker.patch(
            "modules.mega_exporter.requests.get",
            side_effect=[
                MagicMock(status_code=404),
                _artwork_response(),
                img_response,
            ],
        )
        assert _fetch_webp("mega-charizard-x", 6, dest) is True
        assert dest.exists()

    def test_missing_artwork_url_tries_next_slug(self, tmp_path, mocker):
        dest = tmp_path / "mega-venusaur.webp"
        no_url = MagicMock(status_code=200)
        no_url.json.return_value = {"sprites": {"other": {"official-artwork": {}}}}
        img_response = MagicMock()
        img_response.content = _png_bytes()
        mocker.patch(
            "modules.mega_exporter.requests.get",
            side_effect=[no_url, _artwork_response(), img_response],
        )
        assert _fetch_webp("mega-venusaur", 3, dest) is True

    def test_bad_image_bytes_falls_through_to_next_slug(self, tmp_path, mocker):
        # "mega-corrupt" -> 3 slugs: corrupt-mega, corrupt, <ndex>.
        dest = tmp_path / "mega-corrupt.webp"
        bad_img = MagicMock()
        bad_img.content = b"not an image"
        mocker.patch(
            "modules.mega_exporter.requests.get",
            side_effect=[
                _artwork_response(),  # slug 1 json
                bad_img,  # slug 1 image (fails to decode)
                MagicMock(status_code=404),  # slug 2
                MagicMock(status_code=404),  # slug 3
            ],
        )
        assert _fetch_webp("mega-corrupt", 999, dest) is False
        assert dest.with_suffix(".skip").exists()
        assert not dest.exists()

    def test_all_slugs_fail_marks_skip(self, tmp_path, mocker):
        dest = tmp_path / "mega-unknown.webp"
        mocker.patch(
            "modules.mega_exporter.requests.get",
            return_value=MagicMock(status_code=404),
        )
        assert _fetch_webp("mega-unknown", 999, dest) is False
        assert dest.with_suffix(".skip").exists()
        assert not dest.exists()


def _masterfile(pokemon):
    return {"pokemon": pokemon}


@pytest.fixture
def exporter(tmp_path):
    exp = MegaExporter(poliswag=MagicMock())
    exp.output_path = tmp_path / "megas.json"
    exp.sprites_dir = tmp_path / "sprites"
    return exp


class TestExport:
    def test_no_masterfile_returns_false(self, exporter):
        exporter.poliswag.quest_search.masterfile_data = None
        assert exporter.export() is False

    def test_masterfile_missing_pokemon_key_returns_false(self, exporter):
        exporter.poliswag.quest_search.masterfile_data = {}
        assert exporter.export() is False

    def test_pokemon_without_temp_evolutions_skipped(self, exporter):
        exporter.poliswag.quest_search.masterfile_data = _masterfile(
            {"1": {"name": "Bulbasaur", "tempEvolutions": {}}}
        )
        assert exporter.export() is True
        assert json.loads(exporter.output_path.read_text()) == []

    def test_unknown_tevo_id_skipped(self, exporter):
        exporter.poliswag.quest_search.masterfile_data = _masterfile(
            {"1": {"name": "Bulbasaur", "tempEvolutions": {"99": {}}}}
        )
        assert exporter.export() is True
        assert json.loads(exporter.output_path.read_text()) == []

    def test_mega_entry_written_with_full_fields(self, exporter, mocker):
        fetch = mocker.patch("modules.mega_exporter._fetch_webp", return_value=True)
        mocker.patch("modules.mega_exporter.time.sleep")
        exporter.poliswag.quest_search.masterfile_data = _masterfile(
            {
                "3": {
                    "name": "Venusaur",
                    "generation": "Kanto",
                    "types": [{"typeName": "grass"}],
                    "tempEvolutions": {"1": {"firstEnergyCost": 40}},
                }
            }
        )
        assert exporter.export() is True
        entries = json.loads(exporter.output_path.read_text())
        assert len(entries) == 1
        e = entries[0]
        assert e["key"] == "mega-venusaur"
        assert e["label"] == "Mega Venusaur"
        assert e["ndex"] == 3
        assert e["gen"] == "I"
        assert e["types"] == ["grass"]
        assert e["category"] == "mega"
        assert e["released"] is True
        fetch.assert_called_once()

    def test_unreleased_entry_has_released_false(self, exporter, mocker):
        mocker.patch("modules.mega_exporter._fetch_webp", return_value=True)
        mocker.patch("modules.mega_exporter.time.sleep")
        exporter.poliswag.quest_search.masterfile_data = _masterfile(
            {"3": {"name": "Venusaur", "tempEvolutions": {"1": {}}}}
        )
        exporter.export()
        entries = json.loads(exporter.output_path.read_text())
        assert entries[0]["released"] is False

    def test_mega_x_label_and_key_format(self, exporter, mocker):
        mocker.patch("modules.mega_exporter._fetch_webp", return_value=True)
        mocker.patch("modules.mega_exporter.time.sleep")
        exporter.poliswag.quest_search.masterfile_data = _masterfile(
            {"6": {"name": "Charizard", "tempEvolutions": {"2": {}}}}
        )
        exporter.export()
        entries = json.loads(exporter.output_path.read_text())
        assert entries[0]["label"] == "Mega Charizard X"
        assert entries[0]["key"] == "mega-charizard-x"

    def test_primal_category_and_label(self, exporter, mocker):
        mocker.patch("modules.mega_exporter._fetch_webp", return_value=True)
        mocker.patch("modules.mega_exporter.time.sleep")
        exporter.poliswag.quest_search.masterfile_data = _masterfile(
            {"383": {"name": "Groudon", "tempEvolutions": {"4": {}}}}
        )
        exporter.export()
        entries = json.loads(exporter.output_path.read_text())
        assert entries[0]["category"] == "primal"
        assert entries[0]["label"] == "Primal Groudon"

    def test_types_fall_back_to_base_when_tevo_missing_types(self, exporter, mocker):
        mocker.patch("modules.mega_exporter._fetch_webp", return_value=True)
        mocker.patch("modules.mega_exporter.time.sleep")
        exporter.poliswag.quest_search.masterfile_data = _masterfile(
            {
                "1": {
                    "name": "Bulbasaur",
                    "types": [{"typeName": "grass"}, {"typeName": "poison"}],
                    "tempEvolutions": {"1": {}},
                }
            }
        )
        exporter.export()
        entries = json.loads(exporter.output_path.read_text())
        assert entries[0]["types"] == ["grass", "poison"]

    def test_entries_sorted_by_ndex_then_key(self, exporter, mocker):
        mocker.patch("modules.mega_exporter._fetch_webp", return_value=True)
        mocker.patch("modules.mega_exporter.time.sleep")
        exporter.poliswag.quest_search.masterfile_data = _masterfile(
            {
                "150": {"name": "Mewtwo", "tempEvolutions": {"2": {}, "3": {}}},
                "3": {"name": "Venusaur", "tempEvolutions": {"1": {}}},
            }
        )
        exporter.export()
        entries = json.loads(exporter.output_path.read_text())
        assert [e["ndex"] for e in entries] == [3, 150, 150]
        assert entries[1]["key"] < entries[2]["key"]

    def test_skips_sprite_fetch_when_webp_already_exists(self, exporter, mocker):
        fetch = mocker.patch("modules.mega_exporter._fetch_webp")
        exporter.poliswag.quest_search.masterfile_data = _masterfile(
            {"3": {"name": "Venusaur", "tempEvolutions": {"1": {}}}}
        )
        exporter.sprites_dir.mkdir(parents=True)
        (exporter.sprites_dir / "mega-venusaur.webp").touch()
        exporter.export()
        fetch.assert_not_called()

    def test_skips_sprite_fetch_when_skip_marker_exists(self, exporter, mocker):
        fetch = mocker.patch("modules.mega_exporter._fetch_webp")
        exporter.poliswag.quest_search.masterfile_data = _masterfile(
            {"3": {"name": "Venusaur", "tempEvolutions": {"1": {}}}}
        )
        exporter.sprites_dir.mkdir(parents=True)
        (exporter.sprites_dir / "mega-venusaur.skip").touch()
        exporter.export()
        fetch.assert_not_called()
