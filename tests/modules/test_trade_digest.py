from datetime import datetime

from modules.trade_digest import build_digest, describe, group_rows, is_due


def row(**patch):
    out = {
        "discord_id": 1,
        "display_name": "Rui",
        "list": "have",
        "category": "shiny",
        "pokemon_id": 60,
        "form_id": 0,
        "pokemon_name": "Poliwag",
        "form_name": None,
    }
    out.update(patch)
    return out


class TestIsDue:
    """Fires once a morning, and never before nine."""

    def test_waits_for_nine(self):
        assert not is_due(datetime(2026, 9, 21, 8, 59), datetime(2026, 9, 20, 9, 0))

    def test_fires_at_nine(self):
        assert is_due(datetime(2026, 9, 21, 9, 0), datetime(2026, 9, 20, 9, 0))

    def test_does_not_fire_twice_in_one_day(self):
        assert not is_due(datetime(2026, 9, 21, 18, 0), datetime(2026, 9, 21, 9, 0))

    # A bot restart at 10:00 must not re-post the 09:00 digest.
    def test_a_later_start_the_same_day_stays_quiet(self):
        assert not is_due(datetime(2026, 9, 21, 23, 59), datetime(2026, 9, 21, 9, 2))

    def test_first_ever_run_has_no_watermark(self):
        assert is_due(datetime(2026, 9, 21, 9, 0), None)


class TestDescribe:
    def test_names_the_pokemon_and_its_quality(self):
        assert describe(row()) == "Poliwag (Shiny)"

    def test_leaves_a_plain_pokemon_plain(self):
        assert describe(row(category="normal")) == "Poliwag"

    def test_keeps_the_form(self):
        assert describe(row(form_name="Costume 2020")) == "Poliwag Costume 2020 (Shiny)"

    # A want with no species: the category is the whole request.
    def test_says_qualquer_for_a_category_only_want(self):
        assert (
            describe(row(list="want", pokemon_id=0, pokemon_name=None))
            == "qualquer Shiny"
        )

    def test_says_qualquer_pokemon_when_even_the_category_is_open(self):
        assert (
            describe(
                row(list="want", pokemon_id=0, category="normal", pokemon_name=None)
            )
            == "qualquer Pokémon"
        )

    def test_falls_back_to_the_dex_number(self):
        assert describe(row(pokemon_name=None)) == "#60 (Shiny)"


class TestGroupRows:
    def test_splits_each_player_into_haves_and_wants(self):
        groups = group_rows(
            [
                row(),
                row(
                    list="want", pokemon_id=133, pokemon_name="Eevee", category="normal"
                ),
                row(
                    discord_id=2,
                    display_name="Ana",
                    pokemon_id=25,
                    pokemon_name="Pikachu",
                ),
            ]
        )
        assert [g["name"] for g in groups] == ["Rui", "Ana"]
        assert groups[0]["have"] == ["Poliwag (Shiny)"]
        assert groups[0]["want"] == ["Eevee"]
        assert groups[1]["want"] == []


class TestBuildDigest:
    def test_says_what_is_new_and_links_the_page(self):
        embed = build_digest(group_rows([row()]), 1)
        assert "Poliwag (Shiny)" in embed.description
        assert "Rui" in embed.description
        assert embed.url

    # The point of the post: someone who is not in the app yet learns how.
    def test_always_carries_the_join_tip(self):
        embed = build_digest(group_rows([row()]), 1)
        assert "!trades" in embed.description

    def test_separates_what_they_have_from_what_they_want(self):
        embed = build_digest(
            group_rows([row(), row(list="want", pokemon_id=133, pokemon_name="Eevee")]),
            2,
        )
        assert "Tem:" in embed.description
        assert "Procura:" in embed.description

    def test_collapses_a_long_list_rather_than_running_off_the_screen(self):
        rows = [
            row(
                discord_id=n,
                display_name=f"J{n}",
                pokemon_id=100 + n,
                pokemon_name=f"P{n}",
            )
            for n in range(1, 16)
        ]
        embed = build_digest(group_rows(rows), 15)
        assert "e mais 5" in embed.description
        assert len(embed.description) <= 4096
