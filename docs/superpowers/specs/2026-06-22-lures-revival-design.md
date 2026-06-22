# Lures Revival — Design Spec

**Date:** 2026-06-22
**Status:** Approved for planning
**Origin:** Revives the P-82 `!lures` / `!uselure` feature (commit `20c2919`, PR #114), lost in the MAD → Dragonite migration and the monolith → cogs refactor.

## Goal

Let community admins see which scanner accounts are currently free and healthy, and hand them out to people to run in-game lures — tracking a per-account lure budget so the same accounts aren't over-used.

- `!lures` — list free, healthy accounts that still have lures left, with the credentials needed to log in.
- `!uselure USERNAME N` — adjust an account's remaining lure count (negative removes, positive adds).

## Context / current state

- Scanner backend is **Dragonite** (`ghcr.io/unownhash/dragonite-public:v1.18.8`), MariaDB 10.11 in container `db`. Accounts live in schema `dragonite`, table `account`.
- The existing DB user (`DB_USER` = `pogoleiria`) can already read the `dragonite` schema. No new credentials — just a new schema-name config var and a connector instance.
- The tracking table **`poliswag.account_lure` already exists in prod** (PK `username VARCHAR(50)`, `nb_lures INT NOT NULL DEFAULT 12`; ~30 rows at time of writing). It is **absent** from `mock_database/init.sql`, so dev/tests must add it.
- Bot is cog-based; service modules are instantiated on the `Poliswag` bot object in `main.py` and accessed as `self.poliswag.<service>`. DB access is via `modules/database_connector.py` `DatabaseConnector(database=None)` (defaults to `Config.DB_POLISWAG`; pass a schema name for others).

### `dragonite.account` relevant columns

| Column | Type | Use |
|--------|------|-----|
| `username` | varchar(64) PK | identity, shown in `!lures` |
| `password` | varchar(64) | shown in `!lures` (login credential) |
| `banned`, `suspended`, `invalid`, `warn`, `auth_banned` | tinyint flags | health filter |
| `last_selected` | int (unix) | last time dragonite picked the account |
| `last_released` | int (unix) | last time dragonite returned it to the pool |
| `next_available_time` | int (unix) | cooldown gate; unavailable until `now >= next_available_time` |

**Known limitation:** Dragonite's authoritative "in use" set is in-memory scheduler state (exposed only as aggregate counts via `/accounts/stats`, with no usernames). The DB can only *approximate* which accounts are free. This is acceptable: the worst case is listing an account dragonite still holds, causing minor scan contention, not breakage.

## Availability predicate (approved)

An account is "available for lures" when:

```sql
banned = 0 AND suspended = 0 AND invalid = 0 AND warn = 0 AND auth_banned = 0
AND (next_available_time IS NULL OR next_available_time <= UNIX_TIMESTAMP())
AND (last_selected IS NULL OR last_released >= last_selected)
```

(~172 of 193 accounts at time of writing.)

## Components

### 1. Config — `modules/config.py`

Add:
```python
DB_DRAGONITE = os.environ.get("DB_DRAGONITE", "dragonite")
```
Add `DB_DRAGONITE="dragonite"` to `.env.example` (and `.env`).

### 2. Service — `modules/lure_manager.py` (new)

```python
class LureManager:
    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.dragonite_db = DatabaseConnector(Config.DB_DRAGONITE)  # read-only
        self.db = DatabaseConnector(Config.DB_POLISWAG)             # read/write account_lure
```

Methods:

- **`list_available_with_lures() -> list[dict]`**
  1. Query `dragonite.account` with the availability predicate → list of `{"username", "password"}`.
  2. Reconcile with `account_lure`: any available username not present is inserted at the default 12 lures (seeding). Use parameterized queries; seed in a single pass.
  3. Select available accounts that have a tracking row with `nb_lures > 0`, sort **fewest-first**, cap at **5**.
  4. Return `[{"username", "password", "nb_lures"}, ...]`.
  - Cleans up the old bug (commit `20c2919`) where freshly-seeded accounts were appended *after* the 5-cap loop, bypassing the cap and sort. Here seeding happens before selection.

- **`adjust_lure_count(username: str, delta: int) -> int`**
  - `UPDATE account_lure SET nb_lures = GREATEST(nb_lures + %s, 0) WHERE username = %s`.
  - Returns rows affected so the cog can report when `username` is unknown (0 rows).

All queries parameterized via the connector's `params` argument (no f-string interpolation of user input — fixes the SQL-injection shape of the old code).

### 3. Cog — `cogs/lures.py` (new), admin-only

`cog_check` restricts to `Config.ADMIN_USERS_IDS`, mirroring `tracker.py` / `notifications.py`.

- **`!lures`**
  - Calls `list_available_with_lures()`.
  - Empty → embed saying no accounts available.
  - Otherwise embed titled `LISTA DE CONTAS DISPONÍVEIS`, one line per account: `username / password — N lures`.
  - Uses `embeds.build_embed`.

- **`!uselure USERNAME N`**
  - Parse: require exactly 2 args; `N` must be an int. On bad input, reply with usage hint (`!uselure USERNAME NUMERO`) — do not raise.
  - Call `adjust_lure_count`. If 0 rows affected, reply that the account is unknown.
  - On success, reply in PT (`N lure(s) adicionada(s)/removida(s) da conta USERNAME`) with correct singular/plural, and `log_to_file` the action + invoking user.

Register the cog in `main.py`'s cog list and instantiate `LureManager` on the bot object alongside the other services.

### 4. Schema / dev parity

- `migrations/004_add_account_lure.sql`:
  ```sql
  CREATE TABLE IF NOT EXISTS account_lure (
    username VARCHAR(50) NOT NULL PRIMARY KEY,
    nb_lures INT NOT NULL DEFAULT 12
  );
  ```
- Add the `account_lure` table (with a few seed rows) to `mock_database/init.sql`.
- Add a minimal `dragonite` schema with an `account` table (only the columns the predicate needs: `username`, `password`, `banned`, `suspended`, `invalid`, `warn`, `auth_banned`, `last_selected`, `last_released`, `next_available_time`) plus a handful of seed rows covering free/healthy, in-cooldown, and unhealthy cases — so `!lures` works end-to-end in the dev Docker stack. Unit tests still mock the connectors directly (see Testing); the mock DB is for live dev exercise.

## Data flow

```
!lures  → cog → LureManager.list_available_with_lures()
                  ├─ dragonite.account  (SELECT free+healthy username,password)
                  └─ poliswag.account_lure (seed missing @12; SELECT nb_lures>0, ASC, LIMIT 5)
                → embed → channel

!uselure U N → cog (validate) → LureManager.adjust_lure_count(U, N)
                                  └─ poliswag.account_lure (UPDATE GREATEST(nb_lures+N,0))
                                → PT confirmation + log_to_file
```

## Error handling

- DB errors: rely on `DatabaseConnector` retry/reconnect; on failure the service returns empty/0 and the cog shows a friendly message, logging via `self.poliswag.utility.log_to_file(..., "ERROR")`.
- `!uselure` bad args / non-int / unknown username: friendly replies, no exceptions surfaced to the user.
- Non-admin invocation: handled by `cog_check` (silent or standard CheckFailure handling, matching existing cogs).

## Testing

Follow existing patterns (`tests/modules/`, `tests/cogs/`, `pytest` + `mocker`, autouse guard blocks real pymysql connections).

- `tests/modules/test_lure_manager.py`:
  - seeding inserts missing usernames at 12;
  - 5-cap respected even when more than 5 qualify;
  - fewest-first ordering;
  - accounts with `nb_lures = 0` excluded;
  - `adjust_lure_count` floors at 0 and returns rows-affected (0 for unknown user);
  - queries are parameterized (no raw user input in SQL).
- `tests/cogs/test_lures.py`:
  - admin gate (`cog_check`) blocks non-admins;
  - `!lures` empty vs populated formatting;
  - `!uselure` arg parsing: missing args, non-int N, unknown username, success (add & remove, singular/plural).

## Out of scope (YAGNI)

- Old `!logs` / `!commands` help embeds.
- Automatic account expiry / `force_expire` logic.
- Mirroring dragonite's exact in-memory in-use accounting.
- Any change to `!accounts` or `account_monitor`.
