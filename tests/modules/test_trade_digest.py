from datetime import datetime

from modules.config import Config
from modules.trade_digest import build_digest, describe, is_due


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


RUI = f"[Rui]({Config.TRADES_URL.rstrip('/')}/jogador/1)"


def field(embed, name):
    return next((f.value for f in embed.fields if f.name == name), None)


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
        assert describe(row()) == "Poliwag · Shiny"

    def test_leaves_a_plain_pokemon_plain(self):
        assert describe(row(category="normal")) == "Poliwag"

    def test_keeps_the_form(self):
        assert describe(row(form_name="Costume 2020")) == "Poliwag Costume 2020 · Shiny"

    # A want with no species: the category is the whole request.
    def test_says_qualquer_for_a_category_only_want(self):
        assert (
            describe(row(list="want", pokemon_id=0, pokemon_name=None))
            == "Qualquer Shiny"
        )

    def test_says_qualquer_pokemon_when_even_the_category_is_open(self):
        assert (
            describe(
                row(list="want", pokemon_id=0, category="normal", pokemon_name=None)
            )
            == "Qualquer Pokémon"
        )

    def test_falls_back_to_the_dex_number(self):
        assert describe(row(pokemon_name=None)) == "#60 · Shiny"


class TestBuildDigest:
    """Two fields, because people scan for the Pokémon, not for each other."""

    def test_splits_what_is_offered_from_what_is_wanted(self):
        embed = build_digest(
            [
                row(),
                row(
                    list="want",
                    pokemon_id=246,
                    pokemon_name="Larvitar",
                    category="normal",
                ),
            ]
        )
        assert field(embed, "✨ Para trocar") == f"Poliwag · Shiny — {RUI}"
        assert field(embed, "🔍 Procurados") == f"Larvitar — {RUI}"

    def test_gives_each_pokemon_its_own_line(self):
        embed = build_digest(
            [
                row(),
                row(pokemon_id=133, pokemon_name="Eevee", category="hundo"),
            ]
        )
        assert (
            field(embed, "✨ Para trocar")
            == f"Poliwag · Shiny — {RUI}\nEevee · 100IV — {RUI}"
        )

    # A morning with only wants should not carry an empty heading.
    def test_leaves_out_a_side_nobody_added_to(self):
        embed = build_digest(
            [row(list="want", pokemon_id=246, pokemon_name="Larvitar")]
        )
        assert field(embed, "✨ Para trocar") is None
        assert field(embed, "🔍 Procurados") is not None

    def test_shows_a_sprite_of_something_real(self):
        embed = build_digest(
            [
                row(list="want", pokemon_id=0, pokemon_name=None),
                row(pokemon_id=133, pokemon_name="Eevee"),
                row(pokemon_id=1, pokemon_name="Bulbasaur"),
            ]
        )
        assert embed.thumbnail.url.endswith("/UICONS_OS_128/pokemon/133.png")
        assert embed.image.url is None

    # A post of one or two lines is easy to scroll past; the Pokémon is its
    # picture, at 512px, instead of a corner thumbnail.
    def test_a_small_post_shows_the_pokemon_big(self):
        embed = build_digest([row(), row(pokemon_id=133, pokemon_name="Eevee")])
        assert embed.image.url.endswith("/UICONS_OS/pokemon/60.png")
        assert embed.thumbnail.url is None

    def test_asks_for_the_form_sprite_when_there_is_one(self):
        embed = build_digest([row(form_id=2332)])
        assert embed.image.url.endswith("/60_f2332.png")

    # "Qualquer Shiny" has no sprite, and a broken image is worse than none.
    def test_shows_no_sprite_when_nothing_has_a_species(self):
        embed = build_digest([row(list="want", pokemon_id=0, pokemon_name=None)])
        assert embed.thumbnail.url is None
        assert embed.image.url is None

    def test_marks_what_already_has_a_partner(self):
        embed = build_digest(
            [row(matched=1), row(pokemon_id=133, pokemon_name="Eevee", matched=0)]
        )
        assert (
            field(embed, "✨ Para trocar")
            == f"Poliwag · Shiny 🤝 — {RUI}\nEevee · Shiny — {RUI}"
        )
        assert "🤝 já tem par" in embed.footer.text

    def test_no_legend_without_a_match(self):
        embed = build_digest([row(matched=0)])
        assert "🤝" not in embed.footer.text

    def test_a_name_cannot_break_its_link(self):
        embed = build_digest([row(display_name="[x]_y")])
        assert "— [(x)\\_y](" in field(embed, "✨ Para trocar")

    # One player adding forty Pokémon used to fill the whole post.
    def test_collapses_one_players_flood_into_a_line(self):
        flood = [row(pokemon_id=n, pokemon_name=f"P{n}") for n in range(1, 11)]
        other = row(
            discord_id=2, display_name="Ana", pokemon_id=200, pokemon_name="Misdreavus"
        )
        value = field(build_digest(flood + [other]), "✨ Para trocar")
        assert value.count(f"— {RUI}") == 3
        assert "Misdreavus · Shiny — [Ana]" in value
        assert value.endswith(f"*+7 de {RUI}*")

    def test_carries_the_count_and_the_way_in(self):
        embed = build_digest([row(), row(pokemon_id=133, pokemon_name="Eevee")])
        assert "2" in embed.footer.text
        assert "!trades" in embed.footer.text

    # An embed title is a link, but it does not look like one. The address is
    # the whole point of the post, so it is also in the body, where it reads
    # as something to tap.
    def test_shows_the_address_in_the_body(self):
        embed = build_digest([row()])
        assert Config.TRADES_URL in embed.description
        # Comunidade, where everyone's lists are; the bare address opens the
        # reader's own Pokédex.
        assert embed.url.endswith("/procurar")
        assert "pogoleiria.pt/trocas" in embed.description

    def test_writes_the_address_without_the_scheme(self):
        embed = build_digest([row()])
        assert "https://pogoleiria.pt" not in embed.description.split("](")[0]

    # Same colour as every other Poliswag embed; only the layout changes.
    def test_keeps_the_bot_colour_and_links_the_page(self):
        embed = build_digest([row()])
        assert embed.color.value == Config.EMBED_COLOR
        assert embed.url

    def test_collapses_a_long_field_rather_than_losing_it_to_the_api(self):
        embed = build_digest(
            [
                row(
                    discord_id=n,
                    display_name=f"J{n}",
                    pokemon_id=100 + n,
                    pokemon_name=f"P{n}",
                )
                for n in range(1, 21)
            ]
        )
        value = field(embed, "✨ Para trocar")
        assert "e mais 8" in value
        assert len(value) <= 1024


class TestConnectionTimeouts:
    """The digest runs inside the 60s tick; an unbounded read stalls it."""

    def test_connect_sets_timeouts(self):
        import unittest.mock as mock

        from modules.trade_digest import _connect_pool

        with mock.patch("pymysql.connect") as connect:
            _connect_pool()
        kwargs = connect.call_args.kwargs
        assert kwargs["connect_timeout"]
        assert kwargs["read_timeout"]
        assert kwargs["write_timeout"]
