import pytest

from scripts.shiny_linker.lobby import CodeError, Lobby, RunError
from scripts.shiny_linker.proto import blob, fields, integer, one, text, uint

GYM = ("gym1", 39.74, -8.80)
ID_A = "0123456789abcdef"
ID_B = "fedcba9876543210"


class FakeHook:
    def wrap(self, method, payload=b""):
        return integer(4093, method) + blob(4094, payload)


class FakeSession:
    """Answers by method; a marked GET_PLAYER answers as its target method."""

    def __init__(self, replies):
        self.host = b""
        self.replies = {k: list(v) for k, v in replies.items()}
        self.calls = []

    def rpc(self, method, payload=b""):
        if method == 2:
            envelope = fields(payload)
            method, payload = one(envelope, 4093), one(envelope, 4094, b"")
        self.calls.append((method, fields(payload)))
        return self.replies[method].pop(0)


def lobby_proto(invited=(), private=1):
    return (
        blob(1, uint(7) + uint(8))
        + integer(10, private)
        + b"".join(blob(14, i) for i in invited)
    )


def search_reply(name="Rita99", seen="E:abc", result=1, status=1):
    player = blob(1, seen) + blob(2, name)
    return (
        integer(1, status)
        + blob(2, b"host9")
        + blob(3, integer(1, result) + blob(2, player))
    )


def gym_info(seed=555, gym="gym1"):
    fort = blob(1, gym) + blob(20, integer(1, seed))
    return blob(1, blob(1, fort)) + integer(4, 1)


def details(invited=(), free_ticket=0, result=1):
    return blob(1, lobby_proto(invited)) + integer(4, result) + integer(7, free_ticket)


def opened(extra=None):
    replies = {
        156: [gym_info()],
        163: [details()],
        159: [integer(1, 1) + blob(2, lobby_proto())],
    }
    replies.update(extra or {})
    session = FakeSession(replies)
    lobby = Lobby(session, FakeHook(), GYM, sleep=lambda _s: None)
    lobby.open()
    return lobby, session


class TestSearch:
    def test_returns_the_trainer_name_and_the_id_this_account_sees(self):
        session = FakeSession({5012: [search_reply()]})
        lobby = Lobby(session, FakeHook(), GYM)
        assert lobby.search("123456789012") == ("Rita99", "E:abc")
        method, request = session.calls[0]
        assert method == 5012 and one(request, 1) == 10000
        assert text(fields(one(request, 3)), 1) == "123456789012"
        assert session.host == b"host9"

    def test_an_unknown_code_fails_only_that_code(self):
        lobby = Lobby(FakeSession({5012: [search_reply(result=3)]}), FakeHook(), GYM)
        # The game's own result code rides along, so "wrong code" (3) can be
        # told from a search that failed for another reason.
        with pytest.raises(CodeError, match=r"não encontrado \(3\)"):
            lobby.search("123456789012")

    def test_a_proxy_error_stops_the_run(self):
        lobby = Lobby(FakeSession({5012: [integer(1, 6)]}), FakeHook(), GYM)
        with pytest.raises(RunError):
            lobby.search("123456789012")


class TestOpen:
    def test_joins_privately_without_an_entry_cost(self):
        lobby, session = opened()
        method, join = [c for c in session.calls if c[0] == 159][0]
        assert one(join, 1) == 555 and one(join, 2) == b"gym1" and one(join, 4) == 1
        assert 13 not in join  # raid_entry_cost made the server answer NO_TICKET
        assert lobby.lobby_id == [7, 8]

    def test_claims_the_free_ticket_when_the_game_offers_it(self):
        _, session = opened({163: [details(free_ticket=1)], 815: [integer(1, 1)]})
        assert 815 in [c[0] for c in session.calls]

    def test_a_refused_ticket_stops_the_run(self):
        with pytest.raises(RunError, match="passe"):
            opened({163: [details(free_ticket=1)], 815: [integer(1, 4)]})

    def test_a_gym_whose_raid_ended_stops_the_run(self):
        with pytest.raises(RunError, match="raid"):
            opened({156: [blob(1, blob(1, blob(1, "gym1"))) + integer(4, 1)]})

    def test_a_refused_join_stops_the_run(self):
        with pytest.raises(RunError, match="9"):
            opened({159: [integer(1, 9)]})

    def test_a_public_lobby_is_refused(self):
        with pytest.raises(RunError):
            opened({159: [integer(1, 1) + blob(2, lobby_proto(private=0))]})


class TestInvite:
    def test_returns_the_one_new_invited_id(self):
        lobby, session = opened(
            {163: [details(), details(), details([ID_A])], 1504: [integer(1, 1)]}
        )
        assert lobby.invite("E:abc") == ID_A
        method, request = [c for c in session.calls if c[0] == 1504][0]
        assert one(request, 1) == b"E:abc" and one(request, 2) == b"gym1"

    def test_a_second_invite_ignores_the_first_id(self):
        lobby, _ = opened(
            {
                163: [details(), details([ID_A]), details([ID_A, ID_B])],
                1504: [integer(1, 1), integer(1, 1)],
            }
        )
        assert lobby.invite("E:a") == ID_A
        assert lobby.invite("E:b") == ID_B

    def test_a_refused_invite_fails_only_that_code(self):
        lobby, _ = opened({1504: [integer(1, 10)]})
        with pytest.raises(CodeError, match="recusado"):
            lobby.invite("E:abc")

    def test_a_listed_failed_invitee_fails_only_that_code(self):
        lobby, _ = opened({1504: [integer(1, 1) + blob(3, "E:abc")]})
        with pytest.raises(CodeError, match="recusado"):
            lobby.invite("E:abc")

    @pytest.mark.parametrize("result", [4, 5, 6, 7])
    def test_a_dead_lobby_stops_the_run(self, result):
        lobby, _ = opened({1504: [integer(1, result)]})
        with pytest.raises(RunError):
            lobby.invite("E:abc")

    def test_no_new_id_after_every_poll_fails_only_that_code(self):
        lobby, _ = opened({163: [details()] * 7, 1504: [integer(1, 1)]})
        with pytest.raises(CodeError, match="não devolveu"):
            lobby.invite("E:abc")

    def test_an_id_in_another_shape_is_not_stored(self):
        lobby, _ = opened(
            {163: [details(), details(["E:viewer"])], 1504: [integer(1, 1)]}
        )
        with pytest.raises(CodeError, match="formato"):
            lobby.invite("E:abc")

    def test_two_new_ids_at_once_stop_the_run(self):
        lobby, _ = opened(
            {163: [details(), details([ID_A, ID_B])], 1504: [integer(1, 1)]}
        )
        with pytest.raises(RunError, match="mais de um"):
            lobby.invite("E:abc")


class TestLeave:
    def test_leaves_the_lobby_it_opened(self):
        lobby, session = opened({160: [integer(1, 1)]})
        lobby.leave()
        method, request = session.calls[-1]
        assert method == 160 and one(request, 1) == 555
        assert lobby.lobby_id is None

    def test_leaving_without_a_lobby_sends_nothing(self):
        session = FakeSession({})
        Lobby(session, FakeHook(), GYM).leave()
        assert session.calls == []
