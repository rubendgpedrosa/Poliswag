from argparse import Namespace
import pytest

from scripts.background_rule import epoch, insert_sql, make_rule


def args(**patch):
    return Namespace(
        **dict(
            rule_id="sample",
            label="Synthetic test",
            pokemon_id=570,
            form_id=2108,
            costume_id=0,
            probability=0.04,
            starts_at="2026-10-10T14:00:00-07:00",
            ends_at="2026-10-10T17:00:00-07:00",
            bounds=[39, 40, -9, -8],
            enabled=False,
            **patch
        )
    )


def test_explicit_offsets_and_default_disabled():
    rule = make_rule(args())
    assert rule["starts_at"] == epoch("2026-10-10T21:00:00Z")
    assert rule["ends_at"] - rule["starts_at"] == 10800
    assert rule["enabled"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("probability", 0),
        ("probability", float("nan")),
        ("bounds", [39, 40, 170, -170]),
        ("starts_at", "2026-10-10T14:00:00"),
        ("form_id", -1),
    ],
)
def test_rejects_ambiguous_or_invalid_rules(field, value):
    original = args()
    setattr(original, field, value)
    with pytest.raises(ValueError):
        make_rule(original)


def test_sql_labels_cannot_escape_literals():
    original = args()
    original.label = "' ; DROP TABLE test; --"
    sql = insert_sql(make_rule(original))
    assert original.label not in sql
    assert sql.startswith("INSERT INTO poliswag.background_prediction_rule")


def test_a_rule_without_an_image_has_no_image_column():
    assert "image" not in make_rule(args())


def test_the_image_is_a_location_card_file_name_and_reaches_the_sql():
    rule = make_rule(args(image="sb_GoFest2024_radiance.png"))
    assert rule["image"] == "sb_GoFest2024_radiance.png"
    assert "`image`" in insert_sql(rule)


@pytest.mark.parametrize(
    "image",
    [
        "https://example.test/x.png",
        "../x.png",
        "x.png) ; color:red",
        "x.svg",
        "",
    ],
)
def test_rejects_an_image_that_is_not_a_bare_file_name(image):
    with pytest.raises(ValueError):
        make_rule(args(image=image))
