"""Game sources for the watcher (owner, 2026-11-23)."""

from modules import game_sources as gs


def test_parsers():
    assert gs.parse_play_version('x [[["0.437.0"]],[[[33]]]] y') == "0.437.0"
    assert gs.parse_play_version("nada") is None
    page = '<a href="/news/cd-dec">a</a><a href="/en/news/black-friday">b</a><a href="/news/cd-dec">c</a>'
    assert gs.parse_news(page) == ["cd-dec", "black-friday"]
    assert (
        gs.parse_title("<title>Black Friday sale! — Pokémon GO</title>")
        == "Black Friday sale!"
    )
    data = [{"sha": "abc", "commit": {"message": "0.439.0\n\nmore"}}]
    assert gs.parse_commits(data) == [("abc", "0.439.0")]


def test_unseen_is_silent_first_then_reports_new_oldest_first():
    fresh, seen = gs.unseen([], ["c", "b", "a"])
    assert fresh == [] and seen == ["c", "b", "a"]
    fresh, seen = gs.unseen(seen, ["e", "d", "c", "b"])
    assert fresh == ["d", "e"]
    assert seen[:3] == ["e", "d", "c"]
