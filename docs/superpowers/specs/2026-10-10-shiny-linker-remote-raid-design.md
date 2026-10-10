# Shiny linker: a raid elsewhere when there is none here

Owner's request, 2026-10-10. Approved the same day.

## Why

The linker resolves a friend code by inviting the player to a private raid
lobby, so it needs a raid. The scanned areas have none from about 21:00 to
06:00 Lisbon time: a code sent in the evening waits until morning. On
2026-10-10 at 22:00 three codes were pending and the timer skipped every run
with `sem ligação: raid ativa`.

Santa Cruz, California is eight hours behind. Leiria's night is its
afternoon and evening (13:00 to 22:00 there), when raids are running.

## What changes

The local check stays first and stays login-free. Only when the scanned
areas have no usable raid does the linker go to the remote site.

1. `raid ativa` guard: a raid in `golbat.gym` → the run is exactly today's.
   None, and a remote site is configured → the guard passes with
   "nenhuma local; procura no local remoto". None and no site → blocked, as
   today.
2. `conta livre` guard, remote runs only: the listed accounts must also have
   been left alone by Dragonite for 2 hours (`last_selected` and
   `last_released` in `dragonite.account`), so no account is seen in Leiria
   and in California minutes apart.
3. The run logs the account in at the remote site's coordinates and sends
   one map request (GET_MAP_OBJECTS, 106) for the 25 level-15 cells around
   the point.
4. From the reply it takes a gym with a raid that passes today's filter:
   level 1 or 3, not EX-eligible, hatched (a raid Pokémon is present and the
   battle time has passed), 15 or more minutes left; the one ending last.
5. Raid found: the session moves to that gym's coordinates, the hook goes
   in, and lobbies, invitations and account rotation run unchanged. Later
   lobbies use the same gym and the next rested account.
6. No raid found: the session closes, the hook is never attached, the codes
   keep their attempts. The 20-minute interval between runs applies, so the
   site is visited at most three times an hour.

## Failures

| What | Effect |
|---|---|
| Map reply has gyms but no usable raid | Run ends before the hook; codes unpenalised |
| Map request fails, or the reply has no gym at all | Ordinary run error: codes of the first lobby get one more attempt (five and they fail), so a broken remote path cannot repeat for ever |
| The game refuses the pass or the lobby there | Ordinary run error, as today |
| Hook error, hook that did not leave, game restart | Breaker, as today |

## Settings and commands

| | |
|---|---|
| `SHINY_LINKER_REMOTE_SITE=<lat>,<lon>` | The point to look around. Unset: no fallback, today's behaviour |
| `--remote [LAT,LON]` | Skip the local raid and use the remote site (the given point, or the configured one). With `--check` or `--validate` |

`--validate --remote <lat>,<lon>` proves the whole path with the test
account (the jump, the map request, the free pass, the lobby, the ID)
before the variable is set for real codes.

## Code

| File | Change |
|---|---|
| `scripts/shiny_linker/remote.py` (new) | Parse the site, the cell ids (`s2sphere`, already in the linker's venv, imported on use), the map request, the raid picked out of the reply |
| `scripts/shiny_linker/proto.py` | Read a double field |
| `scripts/shiny_linker/rotom.py` | 106 joins the methods the session sends as itself (the scanner agent forwards it; `/root/unonwhash/tools/shiny-event-rate-test.py` used it live) |
| `scripts/shiny_linker/store.py` | `accounts_free(..., rested=seconds)` |
| `scripts/shiny_linker/linker.py`, `__main__.py` | The fallback branch and `--remote` |
| `docs/context/shiny.md` | Linker rules and settings |

Nothing reaches Golbat: the linker is its own Rotom controller and its
replies come back to it alone, so no Californian gym appears on the map or
in Poracle.

## Accepted limits

- After a remote run the account is back in Dragonite's pool. Dragonite may
  pick it for Leiria within two hours; the linker cannot hold it back. The
  owner accepted this (2026-10-10).
- The linker's own use of an account is not in `dragonite.account`, so a
  local run at dawn may pick an account that was in California an hour
  before.
- Whether the game minds one phone having a worker on another continent is
  only known from a live `--validate --remote`.
- One point, one map request. If Santa Cruz turns out to show too few gyms,
  the point moves; a list of points is not built.
