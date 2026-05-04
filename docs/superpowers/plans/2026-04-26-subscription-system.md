# Personal Notification Subscription System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let any server member claim a private Discord channel wired into Poracle-NG, where they control their own pokémon alert rules — locked behind an admin-managed allowlist.

**Architecture:** An admin allowlists a user (`!sub allow @user`). The user then runs `!subscribe`, which creates a private Discord channel, registers it with Poracle-NG via the existing `PoracleClient`, and records the mapping in a new `subscriptions` DB table. The user manages their own rules in that channel via `!track` commands. `!unsubscribe` tears everything down. All Poracle API calls reuse the existing `PoracleClient` already in the bot. Pokemon name resolution is borrowed from the loaded `Notifications` cog.

**Tech Stack:** Python 3.11, discord.py 2.4, PyMySQL, Poracle-NG REST API, existing `PoracleClient`.

---

## Domain glossary

| Term | Meaning |
|---|---|
| **allowlist** | Admin has granted a user permission to subscribe; they haven't claimed their channel yet (`status = 'allowed'`) |
| **active** | User has claimed their channel; Poracle is delivering to it (`status = 'active'`) |
| **subscription channel** | A private Discord text channel owned by one user, registered in Poracle as a `discord:channel` human |
| **ref** | In the Notifications cog, a `ref` is a channel id/mention/name used to identify a Poracle target; subscription channels always use their own channel id as the ref |

---

## File map

```
migrations/002_add_subscriptions.sql   NEW — SQL migration for prod DB
mock_database/init.sql                 MODIFY — add subscriptions table to dev seed
modules/config.py                      MODIFY — add SUBSCRIPTION_CATEGORY_ID, SUBSCRIPTION_AREAS
.env.example                           MODIFY — document new env vars
modules/poracle_client.py              MODIFY — add delete_channel()
modules/subscription_store.py         NEW — thin DB layer for subscriptions table
cogs/subscriptions.py                  NEW — all subscription commands
main.py                                MODIFY — load cogs.subscriptions
tests/modules/test_subscription_store.py  NEW
tests/modules/test_poracle_client.py   MODIFY — add test for delete_channel
tests/cogs/test_subscriptions.py       NEW
README.md                              MODIFY — document new commands
```

---

## Task 1 — DB Schema

**Files:**
- Create: `migrations/002_add_subscriptions.sql`
- Modify: `mock_database/init.sql`
- Test: `tests/modules/test_subscription_store.py` (failing import only — proves table is absent before migration)

---

- [ ] **Step 1.1 — Create the migration file**

`migrations/002_add_subscriptions.sql`:
```sql
CREATE TABLE IF NOT EXISTS `subscriptions` (
  `user_id`    VARCHAR(30)  NOT NULL,
  `channel_id` VARCHAR(30)  DEFAULT NULL,
  `status`     ENUM('allowed','active','suspended') NOT NULL DEFAULT 'allowed',
  `created_at` INT UNSIGNED NOT NULL DEFAULT (UNIX_TIMESTAMP()),
  PRIMARY KEY (`user_id`),
  KEY `ix_subscriptions_channel` (`channel_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

---

- [ ] **Step 1.2 — Add same table to mock seed (dev DB)**

Append at the end of `mock_database/init.sql`, inside the `poliswag` database block (after the `excluded_event_type` table):

```sql
DROP TABLE IF EXISTS `subscriptions`;
CREATE TABLE `subscriptions` (
  `user_id`    VARCHAR(30)  NOT NULL,
  `channel_id` VARCHAR(30)  DEFAULT NULL,
  `status`     ENUM('allowed','active','suspended') NOT NULL DEFAULT 'allowed',
  `created_at` INT UNSIGNED NOT NULL DEFAULT (UNIX_TIMESTAMP()),
  PRIMARY KEY (`user_id`),
  KEY `ix_subscriptions_channel` (`channel_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

---

- [ ] **Step 1.3 — Commit**

```bash
git add migrations/002_add_subscriptions.sql mock_database/init.sql
git commit -m "feat(db): add subscriptions table"
```

---

## Task 2 — SubscriptionStore

**Files:**
- Create: `modules/subscription_store.py`
- Create: `tests/modules/test_subscription_store.py`

---

- [ ] **Step 2.1 — Write failing tests**

`tests/modules/test_subscription_store.py`:
```python
"""Tests for modules.subscription_store.SubscriptionStore."""

from unittest.mock import MagicMock
import pytest
from modules.subscription_store import SubscriptionStore


@pytest.fixture
def store():
    db = MagicMock()
    return SubscriptionStore(db), db


class TestGet:
    def test_returns_row_when_found(self, store):
        s, db = store
        db.get_data_from_database.return_value = [
            {"user_id": "111", "channel_id": "999", "status": "active"}
        ]
        result = s.get("111")
        assert result == {"user_id": "111", "channel_id": "999", "status": "active"}
        sql = db.get_data_from_database.call_args.args[0]
        assert "user_id = %s" in sql

    def test_returns_none_when_absent(self, store):
        s, db = store
        db.get_data_from_database.return_value = []
        assert s.get("111") is None


class TestGetByChannel:
    def test_returns_row_when_found(self, store):
        s, db = store
        db.get_data_from_database.return_value = [
            {"user_id": "111", "channel_id": "999", "status": "active"}
        ]
        result = s.get_by_channel("999")
        assert result["user_id"] == "111"
        sql = db.get_data_from_database.call_args.args[0]
        assert "channel_id = %s" in sql

    def test_returns_none_when_absent(self, store):
        s, db = store
        db.get_data_from_database.return_value = []
        assert s.get_by_channel("999") is None


class TestAddAllowed:
    def test_inserts_with_allowed_status(self, store):
        s, db = store
        s.add_allowed("111")
        sql = db.execute_query_to_database.call_args.args[0]
        assert "INSERT" in sql
        assert "allowed" in sql
        params = db.execute_query_to_database.call_args.kwargs["params"]
        assert "111" in params

    def test_upsert_does_not_downgrade_active_to_allowed(self, store):
        s, db = store
        s.add_allowed("111")
        sql = db.execute_query_to_database.call_args.args[0]
        # ON DUPLICATE KEY UPDATE must NOT unconditionally set status = 'allowed'
        assert "ON DUPLICATE KEY" in sql
        assert "IF(" in sql or "CASE" in sql


class TestActivate:
    def test_sets_status_active_and_records_channel(self, store):
        s, db = store
        s.activate("111", "999")
        sql = db.execute_query_to_database.call_args.args[0]
        assert "active" in sql
        params = db.execute_query_to_database.call_args.kwargs["params"]
        assert "999" in params and "111" in params


class TestRemove:
    def test_deletes_row(self, store):
        s, db = store
        s.remove("111")
        sql = db.execute_query_to_database.call_args.args[0]
        assert "DELETE" in sql
        params = db.execute_query_to_database.call_args.kwargs["params"]
        assert "111" in params


class TestListAll:
    def test_returns_all_rows(self, store):
        s, db = store
        rows = [
            {"user_id": "111", "channel_id": None, "status": "allowed"},
            {"user_id": "222", "channel_id": "888", "status": "active"},
        ]
        db.get_data_from_database.return_value = rows
        assert s.list_all() == rows

    def test_returns_empty_list_when_none(self, store):
        s, db = store
        db.get_data_from_database.return_value = None
        assert s.list_all() == []
```

Run:
```bash
docker compose exec poliswag python -m pytest tests/modules/test_subscription_store.py -x -q
```
Expected: **FAIL** — `ModuleNotFoundError: modules.subscription_store`.

---

- [ ] **Step 2.2 — Implement SubscriptionStore**

`modules/subscription_store.py`:
```python
class SubscriptionStore:
    def __init__(self, db):
        self.db = db

    def get(self, user_id: str) -> dict | None:
        rows = self.db.get_data_from_database(
            "SELECT user_id, channel_id, status FROM subscriptions WHERE user_id = %s",
            params=(user_id,),
        )
        return rows[0] if rows else None

    def get_by_channel(self, channel_id: str) -> dict | None:
        rows = self.db.get_data_from_database(
            "SELECT user_id, channel_id, status FROM subscriptions WHERE channel_id = %s",
            params=(channel_id,),
        )
        return rows[0] if rows else None

    def add_allowed(self, user_id: str) -> None:
        self.db.execute_query_to_database(
            "INSERT INTO subscriptions (user_id, status) VALUES (%s, 'allowed') "
            "ON DUPLICATE KEY UPDATE status = IF(status = 'suspended', 'allowed', status)",
            params=(user_id,),
        )

    def activate(self, user_id: str, channel_id: str) -> None:
        self.db.execute_query_to_database(
            "UPDATE subscriptions SET status = 'active', channel_id = %s WHERE user_id = %s",
            params=(channel_id, user_id),
        )

    def remove(self, user_id: str) -> None:
        self.db.execute_query_to_database(
            "DELETE FROM subscriptions WHERE user_id = %s",
            params=(user_id,),
        )

    def list_all(self) -> list[dict]:
        return (
            self.db.get_data_from_database(
                "SELECT user_id, channel_id, status FROM subscriptions "
                "ORDER BY status, created_at"
            )
            or []
        )
```

---

- [ ] **Step 2.3 — Run tests**

```bash
docker compose exec poliswag python -m pytest tests/modules/test_subscription_store.py -x -q
```
Expected: **all pass**.

---

- [ ] **Step 2.4 — Commit**

```bash
git add modules/subscription_store.py tests/modules/test_subscription_store.py
git commit -m "feat(store): add SubscriptionStore"
```

---

## Task 3 — PoracleClient.delete_channel

**Files:**
- Modify: `modules/poracle_client.py`
- Modify: `tests/modules/test_poracle_client.py`

---

- [ ] **Step 3.1 — Write failing test**

Append to the `TestHumans` class in `tests/modules/test_poracle_client.py`:

```python
    async def test_delete_channel_sends_delete_request(self, client):
        session = _install_session(client, _response(status=204, content_length=0))
        await client.delete_channel(123)
        args, _ = session.request.call_args
        assert args[0] == "DELETE"
        assert args[1] == "http://poracle.test:3030/api/humans/123"
```

Run:
```bash
docker compose exec poliswag python -m pytest tests/modules/test_poracle_client.py::TestHumans::test_delete_channel_sends_delete_request -x -q
```
Expected: **FAIL** — `AttributeError: 'PoracleClient' object has no attribute 'delete_channel'`.

---

- [ ] **Step 3.2 — Add delete_channel to PoracleClient**

In `modules/poracle_client.py`, add after the `set_areas` method:

```python
    async def delete_channel(self, human_id: str | int) -> None:
        await self._request("DELETE", f"/api/humans/{human_id}")
```

---

- [ ] **Step 3.3 — Run test**

```bash
docker compose exec poliswag python -m pytest tests/modules/test_poracle_client.py -x -q
```
Expected: **all pass**.

---

- [ ] **Step 3.4 — Commit**

```bash
git add modules/poracle_client.py tests/modules/test_poracle_client.py
git commit -m "feat(poracle): add delete_channel method"
```

---

## Task 4 — Config

**Files:**
- Modify: `modules/config.py`
- Modify: `.env.example`

---

- [ ] **Step 4.1 — Add config keys**

In `modules/config.py`, add inside the `Config` class after `ACCOUNTS_CHANNEL_ID`:

```python
    # Subscriptions
    SUBSCRIPTION_CATEGORY_ID = int(os.environ.get("SUBSCRIPTION_CATEGORY_ID", "0"))
    SUBSCRIPTION_AREAS = [
        a.strip()
        for a in os.environ.get("SUBSCRIPTION_AREAS", "leiria,marinha").split(",")
        if a.strip()
    ]
```

---

- [ ] **Step 4.2 — Document in .env.example**

Add after the `#DISCORD CHANNELS` block in `.env.example`:

```bash
#SUBSCRIPTIONS
SUBSCRIPTION_CATEGORY_ID="1234567890123456789"  # Discord category for personal notification channels
SUBSCRIPTION_AREAS="leiria,marinha"              # Geofence areas auto-assigned on subscribe
#!SUBSCRIPTIONS
```

---

- [ ] **Step 4.3 — Add to .env.test**

In `.env.test`, add:
```bash
SUBSCRIPTION_CATEGORY_ID=0
SUBSCRIPTION_AREAS=leiria,marinha
```

---

- [ ] **Step 4.4 — Commit**

```bash
git add modules/config.py .env.example .env.test
git commit -m "feat(config): add SUBSCRIPTION_CATEGORY_ID and SUBSCRIPTION_AREAS"
```

---

## Task 5 — Admin Subscription Commands (`!sub`)

This task builds the admin side: allowlisting users and revoking subscriptions.

**Files:**
- Create: `cogs/subscriptions.py`
- Create: `tests/cogs/test_subscriptions.py`

---

- [ ] **Step 5.1 — Write failing tests for admin commands**

`tests/cogs/test_subscriptions.py`:
```python
"""Tests for cogs.subscriptions — admin !sub commands."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.subscriptions import Subscriptions


@pytest.fixture
def cog():
    poliswag = MagicMock()
    poliswag.ADMIN_USERS_IDS = ["42"]
    poliswag.poracle = MagicMock()
    poliswag.poracle.create_channel = AsyncMock()
    poliswag.poracle.set_areas = AsyncMock()
    poliswag.poracle.start = AsyncMock()
    poliswag.poracle.stop = AsyncMock()
    poliswag.poracle.delete_channel = AsyncMock()
    poliswag.poracle.reload = AsyncMock()
    poliswag.poracle.list_pokemon_tracking = AsyncMock(return_value=[])
    poliswag.poracle.add_pokemon_tracking = AsyncMock()
    poliswag.poracle.delete_pokemon_tracking_uid = AsyncMock()
    poliswag.poracle.test_pokemon = AsyncMock()
    poliswag.quest_search.pokemon_name_map = {"25": "pikachu", "248": "tyranitar"}
    poliswag.quest_search.get_pokemon_id_by_pokemon_name_map = MagicMock(
        side_effect=lambda q: [
            pid for pid, name in poliswag.quest_search.pokemon_name_map.items()
            if q.lower() in name
        ]
    )
    with patch("cogs.subscriptions.DatabaseConnector"), \
         patch("cogs.subscriptions.SubscriptionStore") as MockStore:
        c = Subscriptions(poliswag)
        c.store = MagicMock()
    return c


def make_ctx(author_id="42", channel_id="777"):
    ctx = MagicMock()
    ctx.author.id = author_id
    ctx.author.mention = f"<@{author_id}>"
    ctx.channel.id = channel_id
    ctx.send = AsyncMock()
    ctx.guild = MagicMock()
    return ctx


def reply_text(ctx) -> str:
    embed = ctx.send.call_args.kwargs.get("embed")
    if embed is None:
        content = ctx.send.call_args.args[0] if ctx.send.call_args.args else ""
        return str(content)
    parts = []
    if embed.title:
        parts.append(embed.title)
    if embed.description:
        parts.append(embed.description)
    return "\n".join(parts)


class TestSubAllow:
    async def test_adds_user_to_allowlist(self, cog):
        ctx = make_ctx()
        member = MagicMock()
        member.id = 99
        member.mention = "<@99>"
        cog.store.get.return_value = None

        await Subscriptions.allow_cmd.callback(cog, ctx, member)

        cog.store.add_allowed.assert_called_once_with("99")
        assert "99" in reply_text(ctx) or "permitido" in reply_text(ctx).lower()

    async def test_already_allowed_says_so(self, cog):
        ctx = make_ctx()
        member = MagicMock()
        member.id = 99
        member.mention = "<@99>"
        cog.store.get.return_value = {"user_id": "99", "channel_id": None, "status": "allowed"}

        await Subscriptions.allow_cmd.callback(cog, ctx, member)

        cog.store.add_allowed.assert_not_called()
        assert "já" in reply_text(ctx).lower() or "already" in reply_text(ctx).lower()


class TestSubRevoke:
    async def test_no_subscription_says_so(self, cog):
        ctx = make_ctx()
        member = MagicMock()
        member.id = 99
        cog.store.get.return_value = None

        await Subscriptions.revoke_cmd.callback(cog, ctx, member)

        cog.store.remove.assert_not_called()

    async def test_revokes_active_subscription(self, cog):
        ctx = make_ctx()
        member = MagicMock()
        member.id = 99
        cog.store.get.return_value = {
            "user_id": "99", "channel_id": "888", "status": "active"
        }
        channel = AsyncMock()
        ctx.guild.get_channel.return_value = channel

        await Subscriptions.revoke_cmd.callback(cog, ctx, member)

        cog.poliswag.poracle.stop.assert_awaited_once()
        cog.poliswag.poracle.delete_channel.assert_awaited_once()
        channel.delete.assert_awaited_once()
        cog.store.remove.assert_called_once_with("99")


class TestSubList:
    async def test_empty_list(self, cog):
        ctx = make_ctx()
        cog.store.list_all.return_value = []

        await Subscriptions.list_cmd.callback(cog, ctx)

        assert "sem subscrições" in reply_text(ctx).lower() or "nenhum" in reply_text(ctx).lower()

    async def test_shows_subscriptions(self, cog):
        ctx = make_ctx()
        cog.store.list_all.return_value = [
            {"user_id": "99", "channel_id": "888", "status": "active"},
            {"user_id": "77", "channel_id": None, "status": "allowed"},
        ]

        await Subscriptions.list_cmd.callback(cog, ctx)

        text = reply_text(ctx)
        assert "99" in text
        assert "77" in text
```

Run:
```bash
docker compose exec poliswag python -m pytest tests/cogs/test_subscriptions.py -x -q
```
Expected: **FAIL** — `ModuleNotFoundError: cogs.subscriptions`.

---

- [ ] **Step 5.2 — Create cogs/subscriptions.py with admin commands**

`cogs/subscriptions.py`:
```python
import discord
from discord.ext import commands

from modules.config import Config
from modules.database_connector import DatabaseConnector
from modules.poracle_client import PoracleError
from modules.subscription_store import SubscriptionStore


class Subscriptions(commands.Cog):
    """Subscription-based personal notification channels."""

    def __init__(self, poliswag):
        self.poliswag = poliswag
        self.store = SubscriptionStore(DatabaseConnector(Config.DB_POLISWAG))

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    # ---- helpers -------------------------------------------------------

    def _is_admin(self, ctx) -> bool:
        return str(ctx.author.id) in self.poliswag.ADMIN_USERS_IDS

    async def _reply(self, ctx, description: str, *, title: str | None = None, error: bool = False):
        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.red() if error else Config.EMBED_COLOR,
        )
        await ctx.send(embed=embed)

    async def _teardown_subscription(self, ctx, user_id: str) -> bool:
        """Stop Poracle, delete Discord channel, remove DB row. Returns True on success."""
        sub = self.store.get(user_id)
        if not sub:
            await self._reply(ctx, f"Utilizador `{user_id}` não tem subscrição.", error=True)
            return False
        if sub["channel_id"]:
            try:
                await self.poliswag.poracle.stop(sub["channel_id"])
                await self.poliswag.poracle.delete_channel(sub["channel_id"])
            except PoracleError:
                pass
            channel = ctx.guild.get_channel(int(sub["channel_id"]))
            if channel:
                await channel.delete()
        self.store.remove(user_id)
        return True

    # ---- admin group ---------------------------------------------------

    @commands.group(
        name="sub",
        invoke_without_command=True,
        brief="Gestão de subscrições (admin)",
    )
    async def sub(self, ctx):
        if not self._is_admin(ctx):
            return
        await self._reply(
            ctx,
            "`!sub allow @user` — adiciona à allowlist\n"
            "`!sub revoke @user` — remove subscrição\n"
            "`!sub list` — lista todas as subscrições",
            title="!sub — gestão de subscrições",
        )

    @sub.command(name="allow", brief="Adiciona utilizador à allowlist")
    async def allow_cmd(self, ctx, member: discord.Member):
        if not self._is_admin(ctx):
            return
        existing = self.store.get(str(member.id))
        if existing:
            await self._reply(
                ctx,
                f"{member.mention} já está na allowlist (estado: `{existing['status']}`).",
            )
            return
        self.store.add_allowed(str(member.id))
        await self._reply(
            ctx,
            f"✔ {member.mention} (`{member.id}`) permitido a subscrever. "
            "Pode agora usar `!subscribe` em qualquer canal.",
            title="Subscrição autorizada",
        )

    @sub.command(name="revoke", brief="Remove subscrição de um utilizador")
    async def revoke_cmd(self, ctx, member: discord.Member):
        if not self._is_admin(ctx):
            return
        removed = await self._teardown_subscription(ctx, str(member.id))
        if removed:
            await self._reply(
                ctx,
                f"✔ Subscrição de {member.mention} removida.",
                title="Subscrição revogada",
            )

    @sub.command(name="list", brief="Lista todas as subscrições")
    async def list_cmd(self, ctx):
        if not self._is_admin(ctx):
            return
        rows = self.store.list_all()
        if not rows:
            await self._reply(ctx, "Sem subscrições registadas.")
            return
        STATUS_ICON = {"allowed": "⏳", "active": "🟢", "suspended": "🔴"}
        lines = [
            f"{STATUS_ICON.get(r['status'], '?')} <@{r['user_id']}> — "
            f"`{r['status']}`"
            + (f" → <#{r['channel_id']}>" if r["channel_id"] else "")
            for r in rows
        ]
        await self._reply(
            ctx,
            "\n".join(lines)[:4000],
            title=f"Subscrições ({len(rows)})",
        )
```

---

- [ ] **Step 5.3 — Run tests**

```bash
docker compose exec poliswag python -m pytest tests/cogs/test_subscriptions.py::TestSubAllow tests/cogs/test_subscriptions.py::TestSubRevoke tests/cogs/test_subscriptions.py::TestSubList -x -q
```
Expected: **all pass**.

---

- [ ] **Step 5.4 — Commit**

```bash
git add cogs/subscriptions.py tests/cogs/test_subscriptions.py
git commit -m "feat(subs): admin !sub allow / revoke / list commands"
```

---

## Task 6 — User Subscribe / Unsubscribe

**Files:**
- Modify: `cogs/subscriptions.py`
- Modify: `tests/cogs/test_subscriptions.py`

---

- [ ] **Step 6.1 — Write failing tests**

Append to `tests/cogs/test_subscriptions.py`:

```python
class TestSubscribe:
    async def test_not_allowed_is_rejected(self, cog):
        ctx = make_ctx(author_id="77")
        cog.store.get.return_value = None

        await Subscriptions.subscribe_cmd.callback(cog, ctx)

        cog.poliswag.poracle.create_channel.assert_not_awaited()
        assert "autorizado" in reply_text(ctx).lower() or "permitido" in reply_text(ctx).lower()

    async def test_already_subscribed_sends_channel_mention(self, cog):
        ctx = make_ctx(author_id="77")
        cog.store.get.return_value = {
            "user_id": "77", "channel_id": "888", "status": "active"
        }

        await Subscriptions.subscribe_cmd.callback(cog, ctx)

        cog.poliswag.poracle.create_channel.assert_not_awaited()
        assert "888" in reply_text(ctx)

    async def test_creates_channel_and_registers_poracle(self, cog):
        ctx = make_ctx(author_id="77")
        ctx.author.name = "faynn"
        ctx.author.display_name = "Faynn"
        cog.store.get.return_value = {
            "user_id": "77", "channel_id": None, "status": "allowed"
        }
        new_channel = MagicMock()
        new_channel.id = 555
        new_channel.mention = "<#555>"
        ctx.guild.create_text_channel = AsyncMock(return_value=new_channel)

        await Subscriptions.subscribe_cmd.callback(cog, ctx)

        ctx.guild.create_text_channel.assert_awaited_once()
        cog.poliswag.poracle.create_channel.assert_awaited_once_with(555, "notif-faynn")
        cog.poliswag.poracle.set_areas.assert_awaited_once()
        cog.poliswag.poracle.start.assert_awaited_once_with(555)
        cog.store.activate.assert_called_once_with("77", "555")
        assert "555" in reply_text(ctx)


class TestUnsubscribe:
    async def test_no_subscription_says_so(self, cog):
        ctx = make_ctx(author_id="77")
        cog.store.get.return_value = None

        await Subscriptions.unsubscribe_cmd.callback(cog, ctx)

        cog.store.remove.assert_not_called()

    async def test_tears_down_own_subscription(self, cog):
        ctx = make_ctx(author_id="77")
        cog.store.get.return_value = {
            "user_id": "77", "channel_id": "888", "status": "active"
        }
        channel = AsyncMock()
        ctx.guild.get_channel.return_value = channel

        await Subscriptions.unsubscribe_cmd.callback(cog, ctx)

        cog.poliswag.poracle.stop.assert_awaited_once()
        cog.poliswag.poracle.delete_channel.assert_awaited_once()
        channel.delete.assert_awaited_once()
        cog.store.remove.assert_called_once_with("77")
```

Run:
```bash
docker compose exec poliswag python -m pytest tests/cogs/test_subscriptions.py::TestSubscribe tests/cogs/test_subscriptions.py::TestUnsubscribe -x -q
```
Expected: **FAIL**.

---

- [ ] **Step 6.2 — Add subscribe/unsubscribe to cogs/subscriptions.py**

Add after the `list_cmd` method, still inside the `Subscriptions` class:

```python
    # ---- user commands -------------------------------------------------

    @commands.command(
        name="subscribe",
        brief="Cria o teu canal pessoal de notificações",
        help=(
            "Cria um canal privado ligado ao Poracle para as tuas notificações.\n"
            "Requer autorização prévia de um admin (`!sub allow`).\n"
            "Depois usa `!track add <pokémon>` no teu canal para seguir pokémon."
        ),
    )
    async def subscribe_cmd(self, ctx):
        sub = self.store.get(str(ctx.author.id))

        if sub is None:
            await self._reply(
                ctx,
                "Não estás autorizado a subscrever. Pede a um admin para te adicionar com `!sub allow`.",
                error=True,
            )
            return

        if sub["status"] == "active" and sub["channel_id"]:
            await self._reply(
                ctx,
                f"Já tens um canal de notificações: <#{sub['channel_id']}>.",
            )
            return

        channel_name = f"notif-{ctx.author.name.lower()[:20]}"
        category = ctx.guild.get_channel(Config.SUBSCRIPTION_CATEGORY_ID) if Config.SUBSCRIPTION_CATEGORY_ID else None
        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            ctx.author: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                add_reactions=True,
            ),
            ctx.guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_messages=True,
            ),
        }
        channel = await ctx.guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"Notificações pessoais de {ctx.author.display_name}",
        )

        try:
            await self.poliswag.poracle.create_channel(channel.id, channel_name)
            await self.poliswag.poracle.set_areas(channel.id, Config.SUBSCRIPTION_AREAS)
            await self.poliswag.poracle.start(channel.id)
        except PoracleError as e:
            await channel.delete()
            await self._reply(ctx, f"Erro ao registar canal no Poracle: {e}", error=True)
            return

        self.store.activate(str(ctx.author.id), str(channel.id))
        await self._reply(
            ctx,
            f"✔ Canal {channel.mention} criado e activo.\n"
            f"Usa `!track add <pokémon>` lá dentro para começar a seguir.",
            title="Subscrição activada",
        )

    @commands.command(
        name="unsubscribe",
        brief="Remove o teu canal pessoal de notificações",
        help="Apaga o teu canal privado e cancela todos os alertas.",
    )
    async def unsubscribe_cmd(self, ctx):
        removed = await self._teardown_subscription(ctx, str(ctx.author.id))
        if removed:
            await self._reply(
                ctx,
                "✔ A tua subscrição foi cancelada e o canal foi removido.",
                title="Subscrição cancelada",
            )
```

---

- [ ] **Step 6.3 — Run tests**

```bash
docker compose exec poliswag python -m pytest tests/cogs/test_subscriptions.py -x -q
```
Expected: **all pass**.

---

- [ ] **Step 6.4 — Commit**

```bash
git add cogs/subscriptions.py tests/cogs/test_subscriptions.py
git commit -m "feat(subs): !subscribe and !unsubscribe commands"
```

---

## Task 7 — User Tracking Commands (`!track`)

Users run these in their own subscription channel. The commands operate on the current channel without requiring a `ref` argument.

**Files:**
- Modify: `cogs/subscriptions.py`
- Modify: `tests/cogs/test_subscriptions.py`

---

- [ ] **Step 7.1 — Write failing tests**

Append to `tests/cogs/test_subscriptions.py`:

```python
class TestTrackChannelGuard:
    async def test_rejects_non_subscription_channel(self, cog):
        ctx = make_ctx(author_id="77", channel_id="999")
        cog.store.get_by_channel.return_value = None

        await Subscriptions.track_list_cmd.callback(cog, ctx)

        assert "notificações" in reply_text(ctx).lower() or "canal" in reply_text(ctx).lower()

    async def test_rejects_wrong_owner(self, cog):
        ctx = make_ctx(author_id="77", channel_id="888")
        # channel 888 belongs to user 99, not 77
        cog.store.get_by_channel.return_value = {
            "user_id": "99", "channel_id": "888", "status": "active"
        }

        await Subscriptions.track_list_cmd.callback(cog, ctx)

        assert "permissão" in reply_text(ctx).lower() or "autorizado" in reply_text(ctx).lower()


class TestTrackList:
    async def test_shows_current_rules(self, cog):
        ctx = make_ctx(author_id="77", channel_id="888")
        cog.store.get_by_channel.return_value = {
            "user_id": "77", "channel_id": "888", "status": "active"
        }
        cog.poliswag.poracle.list_pokemon_tracking.return_value = [
            {"uid": "1", "pokemon_id": 25, "min_iv": 90, "max_iv": 100, "min_cp": 0, "max_cp": 9000}
        ]

        await Subscriptions.track_list_cmd.callback(cog, ctx)

        text = reply_text(ctx)
        assert "Pikachu" in text or "pikachu" in text.lower()
        assert "90" in text


class TestTrackAdd:
    async def test_adds_rule_to_own_channel(self, cog):
        ctx = make_ctx(author_id="77", channel_id="888")
        cog.store.get_by_channel.return_value = {
            "user_id": "77", "channel_id": "888", "status": "active"
        }

        await Subscriptions.track_add_cmd.callback(cog, ctx, "pikachu")

        cog.poliswag.poracle.add_pokemon_tracking.assert_awaited_once()
        call_args = cog.poliswag.poracle.add_pokemon_tracking.call_args
        assert call_args.args[0] == "888"
        rule = call_args.args[1]
        assert rule["pokemon_id"] == 25

    async def test_unknown_pokemon_rejected(self, cog):
        ctx = make_ctx(author_id="77", channel_id="888")
        cog.store.get_by_channel.return_value = {
            "user_id": "77", "channel_id": "888", "status": "active"
        }

        await Subscriptions.track_add_cmd.callback(cog, ctx, "bogusmon")

        cog.poliswag.poracle.add_pokemon_tracking.assert_not_awaited()
        assert "único" in reply_text(ctx) or "encontrei" in reply_text(ctx).lower()


class TestTrackRemove:
    async def test_removes_by_name(self, cog):
        ctx = make_ctx(author_id="77", channel_id="888")
        cog.store.get_by_channel.return_value = {
            "user_id": "77", "channel_id": "888", "status": "active"
        }
        # Poracle DB mock: uid 5 for pikachu in channel 888
        cog.poliswag.poracle.list_pokemon_tracking.return_value = [
            {"uid": "5", "pokemon_id": 25, "min_iv": 0, "max_iv": 100, "min_cp": 0}
        ]

        await Subscriptions.track_remove_cmd.callback(cog, ctx, "pikachu")

        cog.poliswag.poracle.delete_pokemon_tracking_uid.assert_awaited_once_with(
            "888", "5"
        )
        cog.poliswag.poracle.reload.assert_awaited_once()


class TestTrackTest:
    async def test_sends_test_notification(self, cog):
        ctx = make_ctx(author_id="77", channel_id="888")
        ctx.author.name = "faynn"
        cog.store.get_by_channel.return_value = {
            "user_id": "77", "channel_id": "888", "status": "active"
        }

        await Subscriptions.track_test_cmd.callback(cog, ctx, "pikachu")

        cog.poliswag.poracle.test_pokemon.assert_awaited_once()
        _, target = cog.poliswag.poracle.test_pokemon.call_args.args
        assert target["id"] == "888"
        assert target["type"] == "discord:channel"
```

Run:
```bash
docker compose exec poliswag python -m pytest tests/cogs/test_subscriptions.py::TestTrackChannelGuard tests/cogs/test_subscriptions.py::TestTrackList tests/cogs/test_subscriptions.py::TestTrackAdd tests/cogs/test_subscriptions.py::TestTrackRemove tests/cogs/test_subscriptions.py::TestTrackTest -x -q
```
Expected: **FAIL**.

---

- [ ] **Step 7.2 — Add !track commands to cogs/subscriptions.py**

Add after `unsubscribe_cmd`, still inside the `Subscriptions` class:

```python
    # ---- internal: channel ownership guard -----------------------------

    def _resolve_pokemon(self, name: str) -> int | None:
        qs = self.poliswag.quest_search
        matches = qs.get_pokemon_id_by_pokemon_name_map(name)
        exact = [m for m in matches if qs.pokemon_name_map.get(m, "") == name.lower()]
        if exact:
            return int(exact[0])
        if len(matches) == 1:
            return int(matches[0])
        return None

    def _pokemon_name(self, pokemon_id: int) -> str:
        name_map = self.poliswag.quest_search.pokemon_name_map or {}
        return name_map.get(str(pokemon_id), f"#{pokemon_id}").title()

    async def _assert_channel_owner(self, ctx) -> dict | None:
        """Return the subscription row if ctx.channel belongs to ctx.author; reply and return None otherwise."""
        sub = self.store.get_by_channel(str(ctx.channel.id))
        if sub is None:
            await self._reply(
                ctx,
                "Este comando só funciona no teu canal pessoal de notificações.",
                error=True,
            )
            return None
        if str(ctx.author.id) != sub["user_id"]:
            await self._reply(ctx, "Não tens permissão para gerir este canal.", error=True)
            return None
        return sub

    # ---- !track group --------------------------------------------------

    @commands.group(
        name="track",
        invoke_without_command=True,
        brief="Gere as tuas notificações pessoais",
        help=(
            "Comandos para gerir as notificações no teu canal pessoal.\n"
            "Usa dentro do teu canal criado com `!subscribe`.\n\n"
            "`!track list` — regras actuais\n"
            "`!track add <pokémon[,pokémon…]> [min_iv] [min_cp]` — adicionar\n"
            "`!track remove <pokémon[,pokémon…]|uid>` — remover\n"
            "`!track test <pokémon>` — teste"
        ),
    )
    async def track(self, ctx):
        await self._reply(
            ctx,
            "`!track list` · `!track add <nome> [iv] [cp]` · `!track remove <nome|uid>` · `!track test <nome>`",
            title="!track — as tuas notificações",
        )

    @track.command(name="list", brief="Lista os teus pokémon seguidos")
    async def track_list_cmd(self, ctx):
        sub = await self._assert_channel_owner(ctx)
        if not sub:
            return
        try:
            rules = await self.poliswag.poracle.list_pokemon_tracking(sub["channel_id"])
        except PoracleError as e:
            await self._reply(ctx, f"Erro ao obter regras: {e}", error=True)
            return
        if not rules:
            await self._reply(ctx, "Ainda não segues nenhum pokémon. Usa `!track add <nome>`.")
            return
        lines = []
        for r in sorted(rules, key=lambda x: (x.get("pokemon_id", 0), x.get("min_iv", 0))):
            pid = r.get("pokemon_id", 0)
            name = "Qualquer" if pid == 0 else self._pokemon_name(pid)
            min_iv, max_iv = r.get("min_iv", 0), r.get("max_iv", 100)
            iv = f"IV={min_iv}" if min_iv == max_iv else f"IV {min_iv}-{max_iv}"
            lines.append(f"`{r.get('uid', '?')}` {name} — {iv}")
        await self._reply(ctx, "\n".join(lines)[:4000], title="As tuas notificações")

    @track.command(name="add", brief="Adiciona pokémon às tuas notificações")
    async def track_add_cmd(self, ctx, names: str, min_iv: int = 0, min_cp: int = 0):
        sub = await self._assert_channel_owner(ctx)
        if not sub:
            return
        parsed = [n.strip() for n in names.split(",") if n.strip()]
        results = []
        for name in parsed:
            pid = self._resolve_pokemon(name)
            if pid is None:
                results.append(f"✖ Não encontrei um pokémon único para '{name}'.")
                continue
            try:
                await self.poliswag.poracle.add_pokemon_tracking(
                    sub["channel_id"],
                    {"pokemon_id": pid, "min_iv": min_iv, "min_cp": min_cp},
                )
                suffix = f" (IV≥{min_iv}, CP≥{min_cp})" if (min_iv or min_cp) else ""
                results.append(f"✔ {self._pokemon_name(pid)}{suffix} adicionado.")
            except PoracleError as e:
                results.append(f"✖ {self._pokemon_name(pid)}: {e}")
        if any(line.startswith("✔") for line in results):
            try:
                await self.poliswag.poracle.reload()
            except PoracleError:
                pass
        await self._reply(ctx, "\n".join(results) if results else "Sem alterações.", title="Adicionar pokémon")

    @track.command(name="remove", brief="Remove pokémon das tuas notificações")
    async def track_remove_cmd(self, ctx, target: str):
        sub = await self._assert_channel_owner(ctx)
        if not sub:
            return
        channel_id = sub["channel_id"]

        # UID path
        if target.isdigit():
            try:
                await self.poliswag.poracle.delete_pokemon_tracking_uid(channel_id, target)
                await self.poliswag.poracle.reload()
                await self._reply(ctx, f"✔ Regra `{target}` removida.", title="Remover pokémon")
            except PoracleError as e:
                await self._reply(ctx, f"Erro: {e}", error=True)
            return

        # Name path
        parsed = [n.strip() for n in target.split(",") if n.strip()]
        results = []
        any_removed = False
        for name in parsed:
            pid = self._resolve_pokemon(name)
            if pid is None:
                results.append(f"✖ Não encontrei '{name}'.")
                continue
            try:
                rules = await self.poliswag.poracle.list_pokemon_tracking(channel_id)
            except PoracleError as e:
                results.append(f"✖ Erro: {e}")
                continue
            matching = [r for r in rules if r.get("pokemon_id") == pid]
            if not matching:
                results.append(f"Nenhuma regra de {self._pokemon_name(pid)} encontrada.")
                continue
            for rule in matching:
                try:
                    await self.poliswag.poracle.delete_pokemon_tracking_uid(channel_id, rule["uid"])
                    any_removed = True
                except PoracleError as e:
                    results.append(f"✖ uid={rule['uid']}: {e}")
            results.append(f"✔ {self._pokemon_name(pid)} removido.")
        if any_removed:
            try:
                await self.poliswag.poracle.reload()
            except PoracleError:
                pass
        await self._reply(ctx, "\n".join(results) if results else "Sem alterações.", title="Remover pokémon")

    @track.command(name="test", brief="Envia uma notificação de teste")
    async def track_test_cmd(self, ctx, pokemon: str):
        sub = await self._assert_channel_owner(ctx)
        if not sub:
            return
        pid = self._resolve_pokemon(pokemon)
        if pid is None:
            await self._reply(ctx, f"Não encontrei um pokémon único para '{pokemon}'.", error=True)
            return
        webhook = {
            "pokemon_id": pid,
            "latitude": 39.744,
            "longitude": -8.807,
            "individual_attack": 15,
            "individual_defense": 15,
            "individual_stamina": 15,
            "cp": 3000,
            "pokemon_level": 35,
        }
        target = {
            "id": sub["channel_id"],
            "name": ctx.author.name,
            "type": "discord:channel",
            "language": "en",
        }
        try:
            await self.poliswag.poracle.test_pokemon(webhook, target)
            await self._reply(
                ctx,
                f"✔ Notificação de teste ({self._pokemon_name(pid)}) enviada.",
                title="Teste",
            )
        except PoracleError as e:
            await self._reply(ctx, f"Erro: {e}", error=True)


async def setup(bot):
    await bot.add_cog(Subscriptions(bot))
```

---

- [ ] **Step 7.3 — Run all subscription tests**

```bash
docker compose exec poliswag python -m pytest tests/cogs/test_subscriptions.py -x -q
```
Expected: **all pass**.

---

- [ ] **Step 7.4 — Commit**

```bash
git add cogs/subscriptions.py tests/cogs/test_subscriptions.py
git commit -m "feat(subs): !track add/remove/list/test commands"
```

---

## Task 8 — Wire Up and Document

**Files:**
- Modify: `main.py`
- Modify: `README.md`

---

- [ ] **Step 8.1 — Load the cog in main.py**

In `main.py`, add inside `setup_hook` after `await self.load_extension("cogs.notifications")`:

```python
        await self.load_extension("cogs.subscriptions")
```

---

- [ ] **Step 8.2 — Run full test suite**

```bash
docker compose exec poliswag python -m pytest -x -q
```
Expected: all existing tests plus new ones **pass**.

---

- [ ] **Step 8.3 — Update README.md**

Add a new section under `## Discord commands`:

```markdown
### Subscriptions

Members must be allowlisted by an admin before they can subscribe.

#### Admin (`!sub`)
- `!sub allow @user` — add a user to the allowlist
- `!sub revoke @user` — remove a user's subscription (deletes their channel)
- `!sub list` — show all subscriptions and their status

#### User
- `!subscribe` — create your private notification channel (requires allowlist)
- `!unsubscribe` — delete your channel and cancel all alerts

#### Inside your personal channel (`!track`)
- `!track list` — show your current rules
- `!track add <name[,name…]> [min_iv] [min_cp]` — follow pokémon
- `!track remove <name[,name…]|uid>` — unfollow
- `!track test <pokemon>` — send a test notification to your channel
```

---

- [ ] **Step 8.4 — Apply migration to production**

```bash
make migrate
```

---

- [ ] **Step 8.5 — Reload bot**

```bash
make reload
```

---

- [ ] **Step 8.6 — Commit**

```bash
git add main.py README.md
git commit -m "feat(subs): wire up subscriptions cog and document commands"
```

---

## Self-Review

### Spec coverage

| Requirement | Task |
|---|---|
| User asks bot for notifications | Task 6 (`!subscribe`) |
| Private channel is created | Task 6 (Discord `create_text_channel` + overwrites) |
| Channel registered in Poracle | Task 6 (`create_channel` + `set_areas` + `start`) |
| User manages their own rules | Task 7 (`!track add/remove/list/test`) |
| Subscription gate (allowlist) | Tasks 5 + 6 (`!sub allow`, status check in `subscribe_cmd`) |
| Admin can revoke | Task 5 (`!sub revoke` → teardown) |
| Clean teardown | Tasks 5 + 6 (`_teardown_subscription` stops Poracle, deletes Discord channel, removes DB row) |
| New Poracle `DELETE /api/humans/{id}` | Task 3 (`delete_channel`) |

### Placeholder scan ✅

No TBDs, no "handle edge cases", no "similar to Task N". Every step has complete code.

### Type consistency ✅

- `sub["channel_id"]` is always a `str` (stored as VARCHAR, returned as str from pymysql)
- `PoracleClient` methods take `str | int` for `human_id` — consistent throughout
- `store.activate("77", "555")` — both strings, matches `activate(user_id: str, channel_id: str)`
- `_assert_channel_owner` returns `dict | None` — callers check `if not sub: return`

### Open decisions (intentional)

- **No area picker for subscribers**: all subscribers get `SUBSCRIPTION_AREAS` (configurable via env). Adding per-user area control is a future extension.
- **Channel name collisions**: two users named `faynn` would both get `notif-faynn`. Discord disambiguates with numeric suffixes automatically; the Poracle name would differ by channel ID. Not a bug.
- **`!track` is not rate-limited**: admin `!notify` commands already lack rate limits. Adding them is a future concern.
