"""One private raid lobby: look players up by friend code, invite each, and
read the internal ID the game lists for them.

What the live runs on 2026-10-10 established, and this relies on:
- a friend-code search returns the player's ID *as the searching account
  sees it*; that value is what the invitation takes, and it is not the ID
  the shiny roll uses;
- inviting needs no friendship;
- the lobby's `invited_player_ids` then lists the real ID (16 hex or 21
  digits), one more entry per invitation, so inviting one player at a time
  says whose it is;
- joining with a `raid_entry_cost` is answered NO_TICKET, a plain join works;
- leaving before the battle keeps the raid pass.

Battle start is never sent: the session and the hook both refuse it.
"""

import time

from modules.shiny_roll import is_player_id

from .proto import blob, fields, integer, one, packed_ints, real, text, uint

SEARCH_PLAYER = 10000
# Raid-details reads after an invitation before giving up on its ID.
POLLS = 6
# SendRaidInvitation results that mean the lobby itself is gone or spent:
# lobby not found, past cut-off, no invites remaining, lobby full.
_LOBBY_OVER = (4, 5, 6, 7)


class CodeError(Exception):
    """This one code cannot be linked. The text is shown to the member."""


class RunError(RuntimeError):
    """The run cannot continue. Unresolved codes stay pending."""


class Lobby:
    def __init__(self, session, hook, gym, sleep=time.sleep):
        self.session, self.hook = session, hook
        self.gym_id, self.lat, self.lon = gym
        self.sleep = sleep
        self.raid_seed = None
        self.lobby_id = None
        self.known = []

    def _native(self, method, payload=b""):
        return fields(self.session.rpc(2, self.hook.wrap(method, payload)))

    def search(self, friend_code):
        """(trainer name, the player's ID as this account sees it)."""
        proxy = self._native(
            5012,
            integer(1, SEARCH_PLAYER)
            + blob(2, self.session.host)
            + blob(3, blob(1, friend_code)),
        )
        if one(proxy, 1) not in (1, 2) or not isinstance(one(proxy, 3), bytes):
            raise RunError(f"pesquisa social falhou ({one(proxy, 1, 0)})")
        self.session.host = one(proxy, 2, self.session.host)
        reply = fields(one(proxy, 3))
        player = fields(one(reply, 2, b""))
        if one(reply, 1) != 1 or not text(player, 1) or not text(player, 2):
            raise CodeError("código não encontrado")
        return text(player, 2), text(player, 1)

    def _details(self):
        request = (
            integer(1, self.raid_seed)
            + blob(2, self.gym_id)
            + real(4, self.lat)
            + real(5, self.lon)
            + real(6, self.lat)
            + real(7, self.lon)
        )
        if self.lobby_id:
            request += blob(3, self._lobby_blob())
        return fields(self.session.rpc(163, request))

    def _lobby_blob(self):
        return b"".join(uint(part) for part in self.lobby_id)

    @staticmethod
    def _invited(lobby):
        return [v.decode("utf8") for v in lobby.get(14, []) if isinstance(v, bytes)]

    def open(self):
        here = real(2, self.lat) + real(3, self.lon)
        info = fields(
            self.session.rpc(
                156, blob(1, self.gym_id) + here + real(4, self.lat) + real(5, self.lon)
            )
        )
        if one(info, 4) != 1:
            raise RunError(f"ginásio não respondeu ({one(info, 4, 0)})")
        fort = fields(one(fields(one(info, 1, b"")), 1, b""))
        self.raid_seed = one(fields(one(fort, 20, b"")), 1)
        if (
            text(fort, 1) != self.gym_id
            or not isinstance(self.raid_seed, int)
            or not self.raid_seed
        ):
            raise RunError("o ginásio já não tem raid")

        details = self._details()
        if one(details, 4) != 1:
            raise RunError(f"detalhes da raid falharam ({one(details, 4, 0)})")
        if not one(details, 6, 0) and one(details, 7, 0):
            # Today's free pass is unclaimed. 3 = already awarded, also fine.
            award = self._native(815, blob(1, self.gym_id) + here)
            if one(award, 1) not in (1, 3):
                raise RunError(f"passe diário recusado ({one(award, 1, 0)})")

        reply = self._native(
            159,
            integer(1, self.raid_seed)
            + blob(2, self.gym_id)
            + integer(4, 1)
            + real(5, self.lat)
            + real(6, self.lon)
            + real(7, self.lat)
            + real(8, self.lon),
        )
        lobby = fields(one(reply, 2, b""))
        lobby_id = packed_ints(lobby, 1)
        if one(reply, 1) != 1 or not lobby_id or not one(lobby, 10, 0):
            raise RunError(f"entrada no lobby privado falhou ({one(reply, 1, 0)})")
        self.lobby_id = lobby_id
        self.known = self._invited(lobby)

    def invite(self, seen_id):
        """Invite one player; return the ID the lobby then lists for them."""
        reply = self._native(
            1504,
            blob(1, seen_id)
            + blob(2, self.gym_id)
            + blob(3, self._lobby_blob())
            + real(4, self.lat)
            + real(5, self.lon)
            + integer(6, 1),
        )
        result = one(reply, 1, 0)
        if result in _LOBBY_OVER:
            raise RunError(f"o lobby deixou de aceitar convites ({result})")
        if result != 1 or reply.get(3):
            raise CodeError(f"convite recusado pelo jogo ({result})")
        for _ in range(POLLS):
            lobby = fields(one(self._details(), 1, b""))
            fresh = [value for value in self._invited(lobby) if value not in self.known]
            if len(fresh) > 1:
                raise RunError("mais de um ID novo no lobby")
            if fresh:
                self.known.append(fresh[0])
                if not is_player_id(fresh[0]):
                    raise CodeError("ID com formato inesperado")
                return fresh[0]
            self.sleep(2)
        raise CodeError("o jogo não devolveu o ID")

    def leave(self):
        if not self.lobby_id:
            return
        self._native(
            160,
            integer(1, self.raid_seed)
            + blob(2, self.gym_id)
            + blob(3, self._lobby_blob()),
        )
        self.lobby_id = None
