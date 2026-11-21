"""Images Hermiswag may attach to a reply (owner, 2026-11-21)."""

import json

from modules import reply_images


def raw(image, reply="Aqui está."):
    return json.dumps({"reply": reply, "image": image})


def test_bar_spec_is_validated_and_drawn():
    spec = reply_images.parse_image(
        raw(
            {
                "type": "bar",
                "title": "Spawns por hora",
                "labels": ["10h", "11h"],
                "values": [812, 905.5],
                "unit": "spawns",
            }
        )
    )
    assert spec["labels"] == ["10h", "11h"] and spec["values"] == [812.0, 905.5]
    png = reply_images.render_bar(spec)
    assert png.startswith(b"\x89PNG")


def test_bad_specs_send_no_image():
    for image in (
        {"type": "bar", "labels": ["a"], "values": [-1]},
        {"type": "bar", "labels": ["a", "b"], "values": [1]},
        {"type": "bar", "labels": [str(i) for i in range(30)], "values": [1] * 30},
        {"type": "bar", "labels": ["a"], "values": [True]},
        {"type": "file", "url": "https://evil.test/x.png"},
        {"type": "pokemon", "name": ""},
    ):
        assert reply_images.parse_image(raw(image)) is None
    assert reply_images.parse_image('{"reply": "só texto"}') is None
    assert reply_images.parse_image("não é json") is None


def test_pokemon_spec_and_name_lookup():
    spec = reply_images.parse_image(
        raw({"type": "pokemon", "name": "Dragonite", "shiny": True})
    )
    assert spec == {"type": "pokemon", "name": "Dragonite", "shiny": True}
    assert reply_images.pokemon_id("dragonite", {"149": "Dragonite"}) == 149
    assert reply_images.pokemon_id("Mewthree", {"149": "Dragonite"}) is None
