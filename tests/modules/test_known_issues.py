"""Known Issues watcher (owner, 2026-11-23)."""

from modules import known_issues as ki

SECTION = """
<a href="/hc/en/5-pokemon-go/faq/864-reporting-a-bug/">x</a>
<a href="/hc/en/5-pokemon-go/faq/2659-bisharp-evolution/">y</a>
<a href="/hc/en/5-pokemon-go/faq/2659-bisharp-evolution/">y again</a>
"""


def page(text, status="Working on a fix"):
    return (
        "<title>Bisharp does not progress — Pokémon GO Help Center</title>"
        '<div class="faq-body js-faq-body"><p><strong>Issue description: </strong>'
        f"{text}<br><strong>Issue status:</strong> {status}</p><p>&nbsp;</p></div>"
    )


def test_parse_section_and_article():
    ids = ki.parse_section(SECTION)
    assert [i for i, _ in ids] == [864, 2659]
    art = ki.parse_article(page("A"))
    assert art["title"] == "Bisharp does not progress"
    assert art["status"] == "Working on a fix"
    assert art["body"] == "Issue description: A\nIssue status: Working on a fix"


def test_first_run_is_silent_then_new_and_edits_are_reported():
    a, b = ki.parse_article(page("A")), ki.parse_article(page("B"))
    events, state = ki.decide({}, [864, 2659], 2659, a)
    assert events == []
    events, state = ki.decide(state, [864, 2659], 2659, a)
    assert events == []
    events, state = ki.decide(state, [864, 2659], 2659, b)
    assert events == [("edit", a["body"])]
    assert "-Issue description: A" in ki.diff(a["body"], b["body"])
    # Helpshift flapping back to A, then B again: no more reports.
    for flap in (a, b, a):
        events, state = ki.decide(state, [864, 2659], 2659, flap)
        assert events == []
    c = ki.parse_article(page("C"))
    events, state = ki.decide(state, [864, 2659, 2700], 2700, c)
    assert events == [("new", 2700)]
    events, state = ki.decide(state, [864, 2659, 2700], 2700, c)
    assert events == []
