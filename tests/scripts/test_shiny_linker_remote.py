"""The remote site: what counts as a usable raid in a map reply, and what a
reply without one does."""

import pytest

from scripts.shiny_linker import remote
from scripts.shiny_linker.lobby import RunError
from scripts.shiny_linker.proto import blob, fields, integer, packed_ints, real

NOW = 1_800_000_000  # seconds
SITE = (36.9741, -122.0258)


def raid(level=1, battle=-600, end=1800, pokemon=7):
    """Raid info with times in seconds from NOW."""
    info = (
        integer(3, (NOW + battle) * 1000)
        + integer(4, (NOW + end) * 1000)
        + integer(6, level)
    )
    if pokemon:
        info += blob(5, integer(2, pokemon))
    return info


def fort(fort_id, lat=36.97, lon=-122.02, raid_info=None, ex=False, stop=False):
    data = blob(1, fort_id) + real(3, lat) + real(4, lon)
    if stop:
        data += integer(9, 1)
    if raid_info is not None:
        data += blob(20, raid_info)
    if ex:
        data += integer(31, 1)
    return data


def reply(*forts, status=1):
    cell = b"".join(blob(3, f) for f in forts)
    return blob(1, cell) + integer(2, status)


class Session:
    def __init__(self, *replies):
        self.lat, self.lon = SITE
        self.replies = list(replies)
        self.sent = []

    def rpc(self, method, payload=b""):
        self.sent.append((method, fields(payload)))
        return self.replies.pop(0)


@pytest.fixture
def three_cells(monkeypatch):
    monkeypatch.setattr(remote, "cell_ids", lambda lat, lon: [11, 12, 13])


def find(session, slept=None):
    slept = [] if slept is None else slept
    return remote.find_raid(session, now=lambda: NOW, sleep=slept.append)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("36.9741,-122.0258", SITE),
        (" 36.9741 , -122.0258 ", SITE),
        ("", None),
        (None, None),
        ("santa cruz", None),
        ("1,2,3", None),
        ("95,0", None),
        ("0,181", None),
    ],
)
def test_the_site_is_a_latitude_and_a_longitude(value, expected):
    assert remote.site(value) == expected


@pytest.mark.usefixtures("three_cells")
class TestFindRaid:
    def test_the_request_names_the_cells_and_where_the_account_stands(self):
        session = Session(reply(fort("g1", raid_info=raid())))
        find(session)
        ((method, request),) = session.sent
        assert method == 106
        assert packed_ints(request, 1) == [11, 12, 13]
        assert packed_ints(request, 2) == [0, 0, 0]
        assert request[3] == fields(real(3, SITE[0]))[3]
        assert request[4] == fields(real(4, SITE[1]))[4]

    def test_it_returns_the_gym_whose_raid_ends_last(self):
        session = Session(
            reply(
                fort("soon", raid_info=raid(end=1000)),
                fort("late", lat=36.98, lon=-122.03, raid_info=raid(level=3, end=2400)),
                fort("none"),
            )
        )
        assert find(session) == ("late", 36.98, -122.03)

    @pytest.mark.parametrize(
        "unusable",
        [
            fort("five", raid_info=raid(level=5)),
            fort("ex", raid_info=raid(), ex=True),
            fort("egg", raid_info=raid(battle=600, pokemon=0)),
            fort("unhatched", raid_info=raid(battle=600)),
            fort("ending", raid_info=raid(end=899)),
            fort("quiet"),
        ],
    )
    def test_a_raid_the_linker_cannot_use_is_no_raid(self, unusable):
        with pytest.raises(remote.NoRaid, match="1 ginásios"):
            find(Session(reply(unusable)))

    def test_a_pokestop_is_not_a_gym(self):
        session = Session(*[reply(fort("stop", stop=True))] * remote.LOOKS)
        with pytest.raises(RunError, match="sem ginásios"):
            find(session)

    def test_an_empty_map_is_asked_for_again(self):
        slept = []
        session = Session(reply(), reply(fort("g1", raid_info=raid())))
        assert find(session, slept)[0] == "g1"
        assert len(session.sent) == 2 and len(slept) == 1

    def test_a_map_that_stays_empty_is_a_run_error(self):
        session = Session(*[reply()] * remote.LOOKS)
        with pytest.raises(RunError, match="sem ginásios"):
            find(session)
        assert len(session.sent) == remote.LOOKS

    def test_a_refused_map_request_is_a_run_error(self):
        with pytest.raises(RunError, match=r"não respondeu \(2\)"):
            find(Session(reply(status=2)))


def test_the_cells_are_the_one_under_the_point_and_two_rings():
    s2sphere = pytest.importorskip("s2sphere")
    ids = remote.cell_ids(*SITE)
    centre = s2sphere.CellId.from_lat_lng(s2sphere.LatLng.from_degrees(*SITE)).parent(
        15
    )
    assert len(ids) == 25 and ids == sorted(ids) and centre.id() in ids
    assert all(s2sphere.CellId(i).level() == 15 for i in ids)
