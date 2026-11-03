"""Tests for modules.mbway_requests.

The owner's note is the only place these numbers are read, so what matters is
that the count is right (first ask vs the nth), that the member is named in a
way the owner recognises, and that a failure here never costs the member the
details he just asked for.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from modules.mbway_requests import ordinal, owner_text, record, where_text


def author(ident=42, name="faynn", display="Faynn"):
    return SimpleNamespace(id=ident, name=name, display_name=display)


class TestOrdinal:
    def test_first_and_second_use_the_masculine_ordinal(self):
        assert ordinal(1) == "1.º"
        assert ordinal(2) == "2.º"

    def test_later_requests_read_naturally(self):
        assert ordinal(3) == "3.º"
        assert ordinal(11) == "11.º"


class TestOwnerText:
    def test_first_request_says_so_and_names_the_member(self):
        text = owner_text(
            author(),
            {"requests": 1, "first": datetime(2026, 11, 3)},
            "por mensagem direta",
        )
        assert "Faynn (faynn)" in text
        assert "`42`" in text
        assert "Primeiro pedido." in text

    def test_repeat_request_carries_the_count_and_the_first_date(self):
        text = owner_text(
            author(),
            {"requests": 4, "first": datetime(2026, 10, 12)},
            "no canal #geral de PoGo Leiria",
        )
        assert "4.º pedido" in text
        assert "12/10/2026" in text
        assert "no canal #geral" in text

    def test_unset_details_are_reported_as_the_revolut_answer(self):
        text = owner_text(author(), {"requests": 1}, "por mensagem direta", unset=True)
        assert "Revolut" in text

    def test_the_details_never_appear_in_the_note(self):
        text = owner_text(author(), {"requests": 2}, "por mensagem direta")
        assert "912" not in text


class TestWhereText:
    def test_dm(self):
        ctx = SimpleNamespace(guild=None, channel=None)
        assert where_text(ctx) == "por mensagem direta"

    def test_channel_with_server(self):
        ctx = SimpleNamespace(
            guild=SimpleNamespace(name="PoGo Leiria"),
            channel=SimpleNamespace(name="geral"),
        )
        assert where_text(ctx) == "no canal #geral de PoGo Leiria"


class TestRecord:
    async def test_increments_and_returns_the_new_total(self):
        db = SimpleNamespace(
            execute_query_to_database=AsyncMock(),
            get_data_from_database=AsyncMock(
                return_value=[
                    {
                        "requests": 3,
                        "first_requested_at": datetime(2026, 10, 12),
                        "last_requested_at": datetime(2026, 11, 3),
                    }
                ]
            ),
        )
        tally = await record(db, author())
        assert tally["requests"] == 3
        assert tally["first"] == datetime(2026, 10, 12)
        db.execute_query_to_database.assert_awaited_once()
        assert (
            "ON DUPLICATE KEY UPDATE" in db.execute_query_to_database.await_args.args[0]
        )
        assert db.get_data_from_database.await_args.kwargs["params"] == (42,)

    async def test_a_missing_row_is_reported_as_zero(self):
        db = SimpleNamespace(
            execute_query_to_database=AsyncMock(),
            get_data_from_database=AsyncMock(return_value=[]),
        )
        tally = await record(db, author())
        assert tally["requests"] == 0
