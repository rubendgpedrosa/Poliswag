# Shiny Linker Remote Raid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the scanned areas have no raid, the shiny linker logs in at a configured point elsewhere (Santa Cruz, California), finds a raid on the live map and links the pending codes there.

**Architecture:** A new `scripts/shiny_linker/remote.py` holds everything about the remote site (parse the setting, cell ids, map request, raid picked from the reply). `linker.py` gains one branch: the `raid ativa` guard falls back to the site, the account guard asks for rested accounts, and the first login is followed by one map look before the hook goes in. Everything after that is today's code.

**Tech Stack:** Python 3.11, the linker's hand-rolled protobuf helpers (`proto.py`), `s2sphere` (already in `/opt/shiny-linker/venv`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-10-shiny-linker-remote-raid-design.md`

**Run tests with** (from the worktree root; the bot's image has pytest):

```bash
docker run --rm -v "$PWD":/app -w /app poliswag python -m pytest tests/scripts -q --no-cov -p no:cacheprovider
```

Baseline before this plan: `103 passed, 1 skipped`.

**Field numbers used** (checked against `/root/poliswag-mitm/vendor-sources/Golbat/pogo/vbase.pb.go`):

| Message | Fields |
|---|---|
| `GetMapObjectsProto` | 1 `cell_id` (packed), 2 `since_time_ms` (packed), 3 `player_lat`, 4 `player_lng` |
| `GetMapObjectsOutProto` | 1 `map_cell`, 2 `status` (1 = success) |
| `ClientMapCellProto` | 3 `fort` |
| `PokemonFortProto` | 1 `fort_id`, 3 `latitude`, 4 `longitude`, 9 `fort_type` (0 gym, 1 stop), 20 `raid_info`, 31 `is_ex_raid_eligible` |
| `RaidInfoProto` | 3 `raid_battle_ms`, 4 `raid_end_ms`, 5 `raid_pokemon`, 6 `raid_level` |
| `PokemonProto` | 2 `pokemon_id` |

---

### Task 1: Read a double; let the session send the map request

**Files:**
- Modify: `scripts/shiny_linker/proto.py`
- Modify: `scripts/shiny_linker/rotom.py:26-30`
- Test: `tests/scripts/test_shiny_linker_proto.py`, `tests/scripts/test_shiny_linker_rotom.py`

- [ ] **Step 1: Write the failing tests**

In `tests/scripts/test_shiny_linker_proto.py` add `double` to the import list and append:

```python
def test_a_double_is_read_back_as_written():
    assert double(fields(real(3, -122.0258)), 3) == -122.0258


def test_a_missing_double_is_the_default():
    assert double(fields(integer(1, 5)), 3) == 0.0
```

In `tests/scripts/test_shiny_linker_rotom.py` append:

```python
def test_the_map_request_is_one_the_session_sends():
    s = session([rpc_reply(106, b"map")])
    assert s.rpc(106) == b"map"
```

- [ ] **Step 2: Run them, expect failure**

`ImportError: cannot import name 'double'` and `ValueError: method 106 is not one this session sends`.

- [ ] **Step 3: Implement**

`proto.py`, after `text`:

```python
def double(message, number, default=0.0):
    """A double field's value; the reader keeps fixed fields as raw bytes."""
    value = one(message, number)
    if isinstance(value, bytes) and len(value) == 8:
        return struct.unpack("<d", value)[0]
    return default
```

`rotom.py`, replace the comment and constant:

```python
# What this session may send as itself: GET_PLAYER, the map around it, gym
# info, raid details. All four pass the scanner agent's own allowlist.
# Everything else the linker needs goes through the hook (phone.Hook.wrap)
# as a marked GET_PLAYER. Battle start (165) is in neither list.
DIRECT_METHODS = (2, 106, 156, 163)
```

- [ ] **Step 4: Run the tests, expect pass**

- [ ] **Step 5: Commit** — `The linker's session can ask for the map, and reads a double`

---

### Task 2: `remote.py` — the site, the map request, the raid

**Files:**
- Create: `scripts/shiny_linker/remote.py`
- Test: `tests/scripts/test_shiny_linker_remote.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The remote site: what counts as a usable raid in a map reply, and what a
reply without one does."""

import pytest

from scripts.shiny_linker import remote
from scripts.shiny_linker.lobby import RunError
from scripts.shiny_linker.proto import blob, fields, integer, one, packed_ints, real

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


@pytest.fixture(autouse=True)
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


def test_the_map_request_names_the_cells_and_where_the_account_stands():
    session = Session(reply(fort("g1", raid_info=raid())))
    find(session)
    ((method, request),) = session.sent
    assert method == 106
    assert packed_ints(request, 1) == [11, 12, 13]
    assert packed_ints(request, 2) == [0, 0, 0]
    assert request[3] == fields(real(3, SITE[0]))[3]
    assert request[4] == fields(real(4, SITE[1]))[4]


def test_it_returns_the_gym_whose_raid_ends_last():
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
def test_a_raid_the_linker_cannot_use_is_no_raid(unusable):
    with pytest.raises(remote.NoRaid, match="1 ginásios"):
        find(Session(reply(unusable)))


def test_a_pokestop_is_not_a_gym():
    session = Session(*[reply(fort("stop", stop=True))] * remote.LOOKS)
    with pytest.raises(RunError, match="sem ginásios"):
        find(session)


def test_an_empty_map_is_asked_for_again():
    slept = []
    session = Session(reply(), reply(fort("g1", raid_info=raid())))
    assert find(session, slept)[0] == "g1"
    assert len(session.sent) == 2 and len(slept) == 1


def test_a_map_that_stays_empty_is_a_run_error():
    slept = []
    session = Session(*[reply()] * remote.LOOKS)
    with pytest.raises(RunError, match="sem ginásios"):
        find(session, slept)
    assert len(session.sent) == remote.LOOKS


def test_a_refused_map_request_is_a_run_error():
    with pytest.raises(RunError, match=r"não respondeu \(2\)"):
        find(Session(reply(status=2)))


def test_the_cells_are_the_one_under_the_point_and_two_rings(monkeypatch):
    s2sphere = pytest.importorskip("s2sphere")
    monkeypatch.undo()
    ids = remote.cell_ids(*SITE)
    centre = s2sphere.CellId.from_lat_lng(
        s2sphere.LatLng.from_degrees(*SITE)
    ).parent(15)
    assert len(ids) == 25 and ids == sorted(ids) and centre.id() in ids
    assert all(s2sphere.CellId(i).level() == 15 for i in ids)
```

(`one` is imported for symmetry with the other test files only if used; drop unused imports before committing — ruff will say.)

- [ ] **Step 2: Run, expect failure** — `ImportError: cannot import name 'remote'`.

- [ ] **Step 3: Implement `scripts/shiny_linker/remote.py`**

```python
"""Where a run goes when the scanned areas have no raid: one look at the map
around a fixed point in another time zone, and the raid picked from it.

The point is Santa Cruz, California, eight hours behind: Leiria's night,
when it has no raids, is the afternoon there. The scanner has no data for
it, so the raid is found live with the map request the scanner itself sends
(GET_MAP_OBJECTS). The reply comes back to the linker's session alone;
Golbat never sees it, so nothing Californian reaches the map or Poracle.
"""

import time

from .lobby import RunError
from .proto import blob, double, fields, one, real, text, uint

GET_MAP_OBJECTS = 106
# The rule of Store.raid_gym, applied to a map reply.
LEVELS = (1, 3)
MIN_SECONDS_LEFT = 900
# Dragonite must have left an account alone this long before it is logged
# in at the remote site, so it is not seen in two continents minutes apart.
REST_SECONDS = 7200
# The first map request at a new place can come back empty.
LOOKS = 3


class NoRaid(Exception):
    """The site has gyms but no raid the linker can use. Nobody's fault."""


def site(value):
    """(lat, lon) from "lat,lon"; None when unset or unreadable."""
    try:
        lat, lon = (float(part) for part in (value or "").split(","))
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def cell_ids(lat, lon):
    """The level-15 cell under the point and the two rings around it."""
    import s2sphere  # only the linker's venv has it

    centre = s2sphere.CellId.from_lat_lng(
        s2sphere.LatLng.from_degrees(lat, lon)
    ).parent(15)
    ring = [centre, *centre.get_all_neighbors(15)]
    ids = {cell.id() for cell in ring}
    for cell in ring:
        ids.update(neighbour.id() for neighbour in cell.get_all_neighbors(15))
    return sorted(ids)


def map_request(ids, lat, lon):
    return (
        blob(1, b"".join(uint(cell) for cell in ids))
        + blob(2, b"".join(uint(0) for _ in ids))
        + real(3, lat)
        + real(4, lon)
    )


def _gyms(reply):
    found = []
    for cell in reply.get(1, []):
        for raw in fields(cell).get(3, []):
            fort = fields(raw)
            if one(fort, 9, 0) == 0:  # 1 is a PokéStop
                found.append(fort)
    return found


def _raid_end(fort, now_ms):
    """When this gym's raid ends (ms) if the linker can use it, else 0."""
    raid = fields(one(fort, 20, b""))
    if (
        one(raid, 6, 0) in LEVELS
        and not one(fort, 31, 0)
        and one(fields(one(raid, 5, b"")), 2, 0) > 0
        and one(raid, 3, 0) < now_ms
        and one(raid, 4, 0) > now_ms + MIN_SECONDS_LEFT * 1000
    ):
        return one(raid, 4, 0)
    return 0


def find_raid(session, now=time.time, sleep=time.sleep):
    """(gym id, lat, lon) of the usable raid that ends last around where the
    session stands. NoRaid when the gyms there have none; RunError when the
    map itself could not be read."""
    request = map_request(
        cell_ids(session.lat, session.lon), session.lat, session.lon
    )
    gyms = []
    for look in range(LOOKS):
        if look:
            sleep(2)
        reply = fields(session.rpc(GET_MAP_OBJECTS, request))
        if one(reply, 2) != 1:
            raise RunError(f"mapa remoto não respondeu ({one(reply, 2, 0)})")
        gyms = _gyms(reply)
        if gyms:
            break
    if not gyms:
        raise RunError("mapa remoto sem ginásios")
    now_ms = int(now() * 1000)
    best = max(gyms, key=lambda fort: _raid_end(fort, now_ms))
    if not _raid_end(best, now_ms):
        raise NoRaid(f"sem raid no local remoto ({len(gyms)} ginásios)")
    return text(best, 1), double(best, 3), double(best, 4)
```

- [ ] **Step 4: Run the tests, expect pass** (the cell test is skipped in the bot's image; Task 5 runs it in the linker's venv).

- [ ] **Step 5: Commit** — `The linker can find a raid on the live map around a fixed point`

---

### Task 3: Rested accounts

**Files:**
- Modify: `scripts/shiny_linker/store.py` (`accounts_free`)
- Test: `tests/scripts/test_shiny_linker_store.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
"""The one query of the linker's store that has a rule worth pinning: which
accounts a remote run may take."""

from scripts.shiny_linker.store import Store


def store(rows):
    s = Store.__new__(Store)
    s.dragonite = "dragonite"
    s.asked = []

    def _all(sql, params=()):
        s.asked.append((sql, params))
        return rows

    s._all = _all
    return s


ROWS = [
    {"username": "acct2", "refresh_token": "t2"},
    {"username": "acct1", "refresh_token": "t1"},
]


def test_free_accounts_keep_their_listed_order_and_skip_those_in_use():
    s = store(ROWS)
    assert s.accounts_free(["acct1", "acct3", "acct2"], {"acct3"}) == [
        ("acct1", "t1"),
        ("acct2", "t2"),
    ]
    sql, params = s.asked[0]
    assert params == ("acct1", "acct2")
    assert "last_released" not in sql


def test_rested_accounts_were_left_alone_by_dragonite_that_long():
    s = store(ROWS)
    s.accounts_free(["acct1", "acct2"], set(), rested=7200)
    sql, params = s.asked[0]
    assert "COALESCE(last_selected, 0) < UNIX_TIMESTAMP() - %s" in sql
    assert "COALESCE(last_released, 0) < UNIX_TIMESTAMP() - %s" in sql
    assert params == ("acct1", "acct2", 7200, 7200)
```

- [ ] **Step 2: Run, expect failure** — `TypeError: accounts_free() got an unexpected keyword argument 'rested'`.

- [ ] **Step 3: Implement**

```python
    def accounts_free(self, usernames, in_use, rested=0):
        """[(username, refresh token)] of the listed healthy accounts that no
        Rotom controller is using, in the order they were listed. With
        `rested`, only those Dragonite has neither picked nor released in
        that many seconds."""
        candidates = [name for name in usernames if name not in in_use]
        if not candidates:
            return []
        marks = ",".join(["%s"] * len(candidates))
        rest, params = "", tuple(candidates)
        if rested:
            rest = (
                " AND COALESCE(last_selected, 0) < UNIX_TIMESTAMP() - %s"
                " AND COALESCE(last_released, 0) < UNIX_TIMESTAMP() - %s"
            )
            params += (rested, rested)
        rows = self._all(
            f"SELECT username, refresh_token FROM {self.dragonite}.account "
            f"WHERE username IN ({marks}) AND provider = 'ptc' AND refresh_token <> '' "
            "AND invalid = 0 AND auth_banned = 0 "
            "AND COALESCE(banned, 0) = 0 AND COALESCE(suspended, 0) = 0" + rest,
            params,
        )
        healthy = {row["username"]: row["refresh_token"] for row in rows}
        return [(name, healthy[name]) for name in candidates if name in healthy]
```

- [ ] **Step 4: Run, expect pass**

- [ ] **Step 5: Commit** — `A remote run only takes accounts Dragonite left alone for two hours`

---

### Task 4: The fallback in the linker, and `--remote`

**Files:**
- Modify: `scripts/shiny_linker/linker.py`, `scripts/shiny_linker/__main__.py`
- Test: `tests/scripts/test_shiny_linker_run.py`

- [ ] **Step 1: Extend the fake world and write the failing tests**

In `FakeStore.__init__` add `self.tired = set()` and `self.rested_asked = []`; replace `accounts_free`:

```python
    def accounts_free(self, usernames, in_use, rested=0):
        self.rested_asked.append(rested)
        return [
            (name, token)
            for name, token in self.accounts
            if name in usernames
            and name not in in_use
            and not (rested and name in self.tired)
        ]
```

In `World.__init__` add, before the classes:

```python
        self.remote_gym = ("gymCA", 36.98, -122.03)
        self.sessions = []  # every session opened, to read where it stands
        self.lobby_gyms = []
```

`Session.__init__` keeps its position and registers itself:

```python
                world.events.append("session")
                self.host = b""
                self.lat, self.lon = lat, lon
                self.opened_at = (lat, lon)
                world.sessions.append(self)
```

`Lobby.__init__` becomes `world.lobby_gyms.append(gym)`. Add a method on `World`:

```python
    def find_raid(self, session):
        self.events.append("map")
        if isinstance(self.remote_gym, Exception):
            raise self.remote_gym
        return self.remote_gym
```

and pass `find_raid=self.find_raid` to `Linker(...)` in `World.linker`.

Import `NoRaid`: `from scripts.shiny_linker.remote import NoRaid`. Append the class:

```python
SITE = "36.9741,-122.0258"


class TestRemoteSite:
    """With no raid in the scanned areas, a run goes to the configured point
    elsewhere: it looks at the map there before the hook goes in."""

    def test_a_local_raid_keeps_the_run_at_home(self, world):
        world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run()
        assert "map" not in world.events
        assert world.sessions[0].opened_at == (39.74, -8.80)
        assert world.store.rested_asked == [0]
        assert world.lobby_gyms == [("gym1", 39.74, -8.80)]

    def test_without_a_local_raid_it_finds_one_at_the_site(self, world):
        world.store.gym = None
        outcome = world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run()
        assert world.store.linked == [(1, "Rita99", ID_A)] and "1 ligado" in outcome
        # Logged in there and looked at the map before the hook went in.
        assert world.events == [
            "session",
            "login acct1",
            "map",
            "attach",
            "open",
            "leave",
            "detach",
            "close",
        ]
        (session,) = world.sessions
        assert session.opened_at == (36.9741, -122.0258)
        assert (session.lat, session.lon) == (36.98, -122.03)  # at the gym
        assert world.lobby_gyms == [("gymCA", 36.98, -122.03)]

    def test_the_guard_says_where_it_will_look(self, world):
        world.store.gym = None
        checks, _ = world.linker(SHINY_LINKER_REMOTE_SITE=SITE).survey()
        (raid,) = [c for c in checks if c[0] == "raid ativa"]
        assert raid[1] is True and "nenhuma local" in raid[2]
        assert world.events == []

    def test_an_unreadable_site_is_no_site(self, world):
        world.store.gym = None
        outcome = world.linker(SHINY_LINKER_REMOTE_SITE="santa cruz").run()
        assert "raid ativa" in outcome and world.events == []

    def test_only_accounts_dragonite_left_alone_go_there(self, world):
        world.store.gym = None
        world.store.accounts = [("acct1", "t1"), ("acct2", "t2")]
        world.store.tired = {"acct1"}
        world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run()
        assert world.store.rested_asked == [7200]
        assert "login acct2" in world.events and "login acct1" not in world.events

    def test_with_no_rested_account_nothing_is_touched(self, world):
        world.store.gym = None
        world.store.tired = {"acct1"}
        outcome = world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run()
        assert "conta livre" in outcome
        assert world.events == [] and world.store.started == 0

    def test_no_raid_there_ends_before_the_hook_and_costs_the_codes_nothing(
        self, world
    ):
        world.store.gym = None
        world.remote_gym = NoRaid("sem raid no local remoto (7 ginásios)")
        outcome = world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run()
        assert world.events == ["session", "login acct1", "map", "close"]
        assert world.store.bumped == [] and world.store.breaker is None
        assert "sem raid no local remoto (7 ginásios)" in outcome
        assert world.store.started == 1  # the interval still applies

    def test_a_map_that_cannot_be_read_counts_as_a_failed_run(self, world):
        world.store.gym = None
        world.remote_gym = RunError("mapa remoto sem ginásios")
        outcome = world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run()
        assert "attach" not in world.events
        assert world.store.bumped == [([1], 5)]
        assert "mapa remoto sem ginásios" in outcome

    def test_later_lobbies_stay_at_the_gym_found(self, world):
        world.store.gym = None
        queue(world, 12)
        world.store.accounts = [("acct1", "t1"), ("acct2", "t2")]
        world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run()
        assert len(world.store.linked) == 12
        assert world.events.count("map") == 1 and world.events.count("session") == 1
        assert world.lobby_gyms == [("gymCA", 36.98, -122.03)] * 2

    def test_asked_to_it_skips_a_local_raid(self, world):
        world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run(remote=True)
        assert "map" in world.events
        assert world.lobby_gyms == [("gymCA", 36.98, -122.03)]

    def test_asked_to_without_a_site_it_does_nothing(self, world):
        outcome = world.linker().run(remote=True)
        assert "raid ativa" in outcome and "sem local remoto" in outcome
        assert world.events == []

    def test_validation_can_prove_the_remote_path(self, world):
        world.search["999999999999"] = ("Scanner", "E:b")
        world.store.accounts = [("acct1", "t1"), ("acct2", "t2")]
        outcome = world.linker(SHINY_LINKER_REMOTE_SITE=SITE).run(
            validate=True, remote=True
        )
        assert outcome.startswith("validação OK")
        assert world.events.count("map") == 1 and world.events.count("open") == 2
        assert world.store.started == 0
```

- [ ] **Step 2: Run, expect failure** — `TypeError: Linker.__init__() got an unexpected keyword argument 'find_raid'`.

- [ ] **Step 3: Implement in `linker.py`**

Imports: `from . import remote as _remote`.

`__init__` takes `find_raid=_remote.find_raid` (after `lobby_cls`) and sets:

```python
        self.find_raid = find_raid
        # Where to look for a raid when the scanned areas have none.
        self.remote = _remote.site(env.get("SHINY_LINKER_REMOTE_SITE"))
```

`survey` and the two guards:

```python
    def survey(self, validate=False, stop_early=False, remote=False):
        """([(guard, ok, detail)], context for the run). Changes nothing.
        `remote` skips the local raid and goes straight to the remote site."""
        checks, ctx = [], {"skip_local": remote}
        ...

    def _raid(self, ctx):
        ctx["gym"] = None if ctx["skip_local"] else self.store.raid_gym()
        ctx["remote"] = not ctx["gym"] and self.remote is not None
        if ctx["remote"]:
            where = "procura no local remoto"
            return True, where if ctx["skip_local"] else f"nenhuma local; {where}"
        return ctx["gym"], "sem local remoto" if ctx["skip_local"] else ""

    def _account(self, ctx):
        in_use = self.rotom.accounts_in_use(ctx["rotom"]) if "rotom" in ctx else set()
        # An account that jumps to the remote site must not have been
        # scanning here a moment ago.
        rested = _remote.REST_SECONDS if ctx.get("remote") else 0
        ctx["accounts"] = self.store.accounts_free(self.accounts, in_use, rested)
        # Not always the same one first: the lobbies are spread over the list.
        self.shuffle(ctx["accounts"])
        detail = f"{len(ctx['accounts'])} livres"
        return ctx["accounts"], detail + (
            f", paradas há {rested // 3600} h" if rested else ""
        )
```

`run(self, validate=False, remote=False)` passes `remote=remote` to `survey`.

`_login` and `_take_worker` take a position instead of a gym:

```python
    def _login(self, place, account, token):
        # Re-checked here: minutes may have passed since the guard looked.
        if account in self.rotom.accounts_in_use(self.rotom.status()):
            raise RunError("a conta entrou em uso pelo scanner")
        self.session = self._take_worker(*place)
        ...

    def _take_worker(self, lat, lon):
        # every `self.rotom.Session(gym[1], gym[2])` becomes `self.rotom.Session(lat, lon)`
```

`_link`:

```python
        gym, accounts, pid = ctx["gym"], ctx["accounts"], ctx["pid"]
        # Where the account stands: at the gym, or at the remote site until
        # a gym is found there.
        place = self.remote if ctx["remote"] else gym[1:]
        ...
        def send_detach(payload):
            ...
                self._login(place, *current)
        ...
                if turn == 0:
                    unsettled.extend(codes)
                    self._login(place, *current)
                    if ctx["remote"]:
                        gym = self.find_raid(self.session)
                        place = gym[1:]
                        self.session.lat, self.session.lon = place
                    attach_attempted = True
                    hook.attach(pid)
                    started = self.clock()
        ...
        except self.rotom.NoWorker:
            error, penalise = "sem worker para a ligação", False
        except _remote.NoRaid as problem:
            # Gyms there, none with a raid to use: the codes lose nothing.
            error, penalise = str(problem), False
        except Exception as problem:
```

The module docstring gains a paragraph:

```
With no raid in the scanned areas and a remote site configured, the run
happens there instead (remote.py): an account Dragonite has left alone for
two hours logs in at the site and looks at the map once; the hook only goes
in when that shows a raid.
```

`__main__.py`: docstring line `--remote [LAT,LON]  skip the local raid: use the remote site (this point, or SHINY_LINKER_REMOTE_SITE)`, and

```python
    parser.add_argument("--remote", nargs="?", const="", metavar="LAT,LON")
    ...
    load_dotenv(REPO / ".env")
    if args.remote:
        os.environ["SHINY_LINKER_REMOTE_SITE"] = args.remote
    remote = args.remote is not None
    ...
            checks, _ = linker.survey(validate=False, remote=remote)
    ...
        outcome = linker.run(validate=args.validate, remote=remote)
```

- [ ] **Step 4: Run the whole `tests/scripts` suite, expect all pass**

- [ ] **Step 5: Commit** — `With no raid here, the linker finds one at a remote site`

---

### Task 5: Docs, lint, the cell test in the linker's venv

**Files:**
- Modify: `docs/context/shiny.md` (Linker rules, Settings)

- [ ] **Step 1:** In "Linker rules", after the first bullet, add:

```markdown
- No raid here (every night, about 21:00 to 06:00): with
  `SHINY_LINKER_REMOTE_SITE` set, the run happens at that point instead
  (`remote.py`; Santa Cruz, California, where it is afternoon). Only accounts
  Dragonite has not picked or released for 2 h go; the account logs in there,
  asks for the map once (GET_MAP_OBJECTS, 25 cells) and takes a gym whose
  raid passes the same rule. The hook goes in only if there is one; a site
  with gyms but no raid costs the codes nothing. The reply never reaches
  Golbat. `--remote [LAT,LON]` forces the site (with `--check`/`--validate`).
```

and to the Settings table:

```markdown
| `SHINY_LINKER_REMOTE_SITE` | `lat,lon` to look for a raid around when the scanned areas have none; unset = no fallback |
```

- [ ] **Step 2:** Lint and format: `docker run --rm -v "$PWD":/app -w /app poliswag sh -c "python -m ruff check scripts tests/scripts && python -m black --check scripts tests/scripts"` — expect clean.

- [ ] **Step 3:** The cell test where `s2sphere` exists: `/opt/shiny-linker/venv/bin/python -c "from scripts.shiny_linker import remote; ids = remote.cell_ids(36.9741, -122.0258); print(len(ids))"` — expect `25`.

- [ ] **Step 4:** Full suite: `docker run --rm -v "$PWD":/app -w /app poliswag python -m pytest -q -p no:cacheprovider` — expect no failures.

- [ ] **Step 5: Commit** — `Context: the linker's remote site`

---

### After the plan (owner-approved in the spec)

1. Fast-forward `main` in `/root/Poliswag` (the timer runs from there; with the variable unset nothing changes).
2. `python -m scripts.shiny_linker --check --remote 36.9741,-122.0258`, then `--validate --remote 36.9741,-122.0258`.
3. Only if validation passes: add `SHINY_LINKER_REMOTE_SITE=36.9741,-122.0258` to `/root/Poliswag/.env`.
