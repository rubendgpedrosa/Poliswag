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
    request = map_request(cell_ids(session.lat, session.lon), session.lat, session.lon)
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
