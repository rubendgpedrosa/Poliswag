# Events Role Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A button Poliswag posts in the announcements channel that lets any member give themselves the permanent **Eventos** role — which a channel overwrite turns into access to the current event channel — and take it back.

**Architecture:** One new cog, `cogs/event_panel.py`, holding a persistent `discord.ui.View` (`timeout=None`, fixed `custom_id`) plus the `!eventpanel` command group. The view's button delegates to a plain async method `handle_click(interaction)` so tests call it directly without going through discord.py's component dispatch. The role is resolved **by ID** from the guild behind the panel channel, which makes the button work identically in a DM (where `interaction.guild` is `None`). `modules/role_manager.py` and `cogs/moderation.py` are not touched — see the spec's *Why not the existing path*.

**Tech Stack:** Python 3.11, discord.py, pytest + pytest-mock (`asyncio_mode = "auto"`, so async tests need no decorator).

**Spec:** `docs/superpowers/specs/2026-09-21-events-role-panel-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `cogs/event_panel.py` (create) | Panel copy constants, `EventPanelView`, `EventPanel` cog with `!eventpanel` / `test` / `clear`. Everything about this feature lives here. |
| `modules/config.py` (modify) | Two new env-backed ints: `EVENT_PANEL_CHANNEL_ID`, `EVENTS_ROLE_ID`. |
| `main.py` (modify) | Load the cog, resolve `EVENT_PANEL_CHANNEL`, register the persistent view. |
| `.env.test` (modify) | Dummy ids so `Config` has non-zero values under pytest. |
| `tests/cogs/test_event_panel.py` (create) | All tests for the above. |
| `docs/context.md` (modify) | Cog map row + env var list. |

**Run tests with:** `pytest tests/cogs/test_event_panel.py -v` (locally; `make test` runs the full suite in Docker).

**Two facts verified against discord.py 2.4.0 before this plan was written — do not re-litigate them:**

1. A cog's command object is called as `cog.command_name(cog, ctx, *args)`. `Command.__call__` only injects the cog itself once `add_cog` has run, which these tests never do, so passing `cog` explicitly is correct.
2. `discord.ui.View.__init__` calls `asyncio.get_running_loop()`. **Any test that constructs `EventPanelView` must be `async def`**, or it dies with `RuntimeError: no running event loop`. Same reason `add_view` goes in `setup_hook` and not in `Poliswag.__init__`.

---

### Task 1: Config values and the view's happy path

**Files:**
- Modify: `modules/config.py` (channel block, after `TRAP_CHANNEL_ID`)
- Modify: `.env.test`
- Create: `cogs/event_panel.py`
- Create: `tests/cogs/test_event_panel.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/cogs/test_event_panel.py`:

```python
"""Tests for cogs.event_panel.

The button's work happens in EventPanelView.handle_click, which the
decorated discord.ui button only forwards to -- so the tests call
handle_click directly instead of going through component dispatch.
"""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from cogs.event_panel import EventPanelView
from modules.config import Config

_ROLE_ID = 4242
_USER_ID = 123


def _role(position=1):
    role = MagicMock()
    role.id = _ROLE_ID
    role.position = position
    return role


def _member(roles=()):
    member = MagicMock()
    member.id = _USER_ID
    member.roles = list(roles)
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    return member


def _guild(role=None, member=None, bot_top_position=10):
    guild = MagicMock()
    guild.get_role.return_value = role
    guild.get_member.return_value = member
    guild.fetch_member = AsyncMock(return_value=member)
    guild.me.top_role.position = bot_top_position
    guild.members = [member] if member is not None else []
    return guild


def _interaction(guild=None, user_id=_USER_ID):
    interaction = MagicMock()
    interaction.guild = guild
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.response.send_message = AsyncMock()
    return interaction


@pytest.fixture
def poliswag():
    bot = MagicMock()
    bot.ADMIN_USERS_IDS = ["111"]
    bot.utility.log_to_file = MagicMock()
    bot.utility.send_embed_to_channel = AsyncMock()
    bot.MOD_CHANNEL = MagicMock()
    bot.EVENT_PANEL_CHANNEL = MagicMock()
    bot.EVENT_PANEL_CHANNEL.send = AsyncMock()
    return bot


@pytest.fixture(autouse=True)
def _role_id(mocker):
    mocker.patch.object(Config, "EVENTS_ROLE_ID", _ROLE_ID)


class TestHandleClick:
    async def test_grants_the_role_when_the_member_does_not_have_it(self, poliswag):
        role = _role()
        member = _member()
        interaction = _interaction(guild=_guild(role=role, member=member))

        await EventPanelView(poliswag).handle_click(interaction)

        member.add_roles.assert_awaited_once()
        assert member.add_roles.await_args.args[0] is role
        member.remove_roles.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True

    async def test_removes_the_role_when_the_member_already_has_it(self, poliswag):
        role = _role()
        member = _member(roles=[role])
        interaction = _interaction(guild=_guild(role=role, member=member))

        await EventPanelView(poliswag).handle_click(interaction)

        member.remove_roles.assert_awaited_once()
        assert member.remove_roles.await_args.args[0] is role
        member.add_roles.assert_not_awaited()

    async def test_the_two_replies_differ(self, poliswag):
        role = _role()
        joined = _interaction(guild=_guild(role=role, member=_member()))
        left = _interaction(guild=_guild(role=role, member=_member(roles=[role])))

        await EventPanelView(poliswag).handle_click(joined)
        await EventPanelView(poliswag).handle_click(left)

        assert (
            joined.response.send_message.await_args.args[0]
            != left.response.send_message.await_args.args[0]
        )

    async def test_the_view_is_persistent(self, poliswag):
        """timeout=None and a fixed custom_id are what let bot.add_view
        revive panels from past events after a restart."""
        view = EventPanelView(poliswag)
        assert view.timeout is None
        assert [child.custom_id for child in view.children] == ["event_panel:toggle"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/cogs/test_event_panel.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'cogs.event_panel'`.

- [ ] **Step 3: Add the config values**

In `modules/config.py`, in the `# Channels` block immediately after the `TRAP_CHANNEL_ID` line:

```python
    # Announcements channel holding the !eventpanel opt-in message, and the
    # permanent Eventos role it grants. The event's own channel is not
    # configured -- it is written straight into the panel copy in
    # cogs/event_panel.py, which is edited per event anyway.
    EVENT_PANEL_CHANNEL_ID = int(os.environ.get("EVENT_PANEL_CHANNEL_ID", "0"))
    EVENTS_ROLE_ID = int(os.environ.get("EVENTS_ROLE_ID", "0"))
```

In `.env.test`, after the `QUEST_CHANNEL_ID=6` line:

```
EVENT_PANEL_CHANNEL_ID=7
EVENTS_ROLE_ID=8
```

- [ ] **Step 4: Write the view**

Create `cogs/event_panel.py`:

```python
import discord

from modules.config import Config

# Edited per event: the channel mention below is the event channel the
# Eventos role unlocks. Nothing else needs changing -- the role is
# permanent and shared by every event.
_PANEL_TITLE = "🎉 Canal de eventos"
_PANEL_BODY = (
    "Carrega no botão para receberes o cargo **Eventos** e veres o canal "
    "do próximo evento: <#1551534974413443082>.\n\n"
    "Carrega outra vez para saíres e deixares de ver os canais de eventos."
)
_BUTTON_LABEL = "Quero participar"
_BUTTON_CUSTOM_ID = "event_panel:toggle"

_AUDIT_REASON = "Auto-atribuição via !eventpanel"

_JOINED = "✅ Já tens o cargo **Eventos** — o canal do evento aparece-te agora."
_LEFT = "👋 Removi-te o cargo **Eventos**. Carrega outra vez quando quiseres voltar."
_ERR_CONFIG = "⚠️ Configuração inválida — avisa um admin."
_ERR_NOT_MEMBER = "⚠️ Não te encontro no servidor. Entra lá primeiro."
_ERR_FAILED = "⚠️ Não consegui alterar o teu cargo. Já avisei os admins."


class EventPanelView(discord.ui.View):
    """Persistent view: timeout=None plus a fixed custom_id, so panels
    posted for past events keep working after every restart, provided
    main.py registers it with bot.add_view."""

    def __init__(self, poliswag):
        super().__init__(timeout=None)
        self.poliswag = poliswag

    @discord.ui.button(
        label=_BUTTON_LABEL,
        emoji="🎉",
        style=discord.ButtonStyle.success,
        custom_id=_BUTTON_CUSTOM_ID,
    )
    async def _toggle_button(self, interaction, button):
        await self.handle_click(interaction)

    def _guild(self, interaction):
        """In a DM interaction.guild is None, so fall back to the guild
        behind the panel channel -- the bot serves a single guild."""
        if interaction.guild is not None:
            return interaction.guild
        channel = getattr(self.poliswag, "EVENT_PANEL_CHANNEL", None)
        return channel.guild if channel is not None else None

    async def handle_click(self, interaction):
        guild = self._guild(interaction)
        role = guild.get_role(Config.EVENTS_ROLE_ID) if guild is not None else None
        if role is None:
            self.poliswag.utility.log_to_file(
                f"[EVENTPANEL] role {Config.EVENTS_ROLE_ID} not found", "ERROR"
            )
            await interaction.response.send_message(_ERR_CONFIG, ephemeral=True)
            return

        member = guild.get_member(interaction.user.id)
        if member is None:
            await interaction.response.send_message(_ERR_NOT_MEMBER, ephemeral=True)
            return

        had_role = role in member.roles
        if had_role:
            await member.remove_roles(role, atomic=True, reason=_AUDIT_REASON)
        else:
            await member.add_roles(role, atomic=True, reason=_AUDIT_REASON)

        await interaction.response.send_message(
            _LEFT if had_role else _JOINED, ephemeral=True
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/cogs/test_event_panel.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add cogs/event_panel.py tests/cogs/test_event_panel.py modules/config.py .env.test
git commit -F - <<'EOF'
Add the button that hands out the Eventos role

The role is resolved by id from the guild, not by name, so renaming it
does not silently break the panel.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Bf8ALc6e3hTDkSFw9QeK6n
EOF
```

---

### Task 2: The click's failure paths

Nothing here may leave the interaction unanswered — an unanswered interaction shows the clicker a red "interaction failed" with no explanation.

**Files:**
- Modify: `cogs/event_panel.py` (`handle_click`)
- Test: `tests/cogs/test_event_panel.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/cogs/test_event_panel.py`:

```python
class TestHandleClickFailures:
    async def test_missing_role_warns_the_user_and_logs(self, poliswag):
        interaction = _interaction(guild=_guild(role=None, member=_member()))

        await EventPanelView(poliswag).handle_click(interaction)

        interaction.response.send_message.assert_awaited_once()
        assert "admin" in interaction.response.send_message.await_args.args[0]
        assert poliswag.utility.log_to_file.call_args.args[1] == "ERROR"

    async def test_dm_click_resolves_the_member_through_the_panel_channel(
        self, poliswag
    ):
        role = _role()
        member = _member()
        guild = _guild(role=role, member=member)
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        interaction = _interaction(guild=None)

        await EventPanelView(poliswag).handle_click(interaction)

        guild.get_member.assert_called_once_with(_USER_ID)
        member.add_roles.assert_awaited_once()

    async def test_dm_click_falls_back_to_fetching_the_member(self, poliswag):
        """An uncached member must not look like a non-member."""
        role = _role()
        member = _member()
        guild = _guild(role=role, member=member)
        guild.get_member.return_value = None
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        interaction = _interaction(guild=None)

        await EventPanelView(poliswag).handle_click(interaction)

        guild.fetch_member.assert_awaited_once_with(_USER_ID)
        member.add_roles.assert_awaited_once()

    async def test_non_member_is_told_so_and_no_role_is_touched(self, poliswag):
        role = _role()
        guild = _guild(role=role, member=None)
        guild.fetch_member = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "unknown member")
        )
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        interaction = _interaction(guild=None)

        await EventPanelView(poliswag).handle_click(interaction)

        assert "servidor" in interaction.response.send_message.await_args.args[0]

    async def test_forbidden_apologises_and_tells_the_mods(self, poliswag):
        role = _role()
        member = _member()
        member.add_roles = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), "missing perms")
        )
        interaction = _interaction(guild=_guild(role=role, member=member))

        await EventPanelView(poliswag).handle_click(interaction)

        interaction.response.send_message.assert_awaited_once()
        assert poliswag.utility.log_to_file.call_args.args[1] == "ERROR"
        poliswag.utility.send_embed_to_channel.assert_awaited_once()
        assert (
            poliswag.utility.send_embed_to_channel.await_args.args[0]
            is poliswag.MOD_CHANNEL
        )

    async def test_forbidden_without_a_mod_channel_still_replies(self, poliswag):
        role = _role()
        member = _member()
        member.add_roles = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), "missing perms")
        )
        poliswag.MOD_CHANNEL = None
        interaction = _interaction(guild=_guild(role=role, member=member))

        await EventPanelView(poliswag).handle_click(interaction)

        interaction.response.send_message.assert_awaited_once()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/cogs/test_event_panel.py::TestHandleClickFailures -v`
Expected: FAIL — `test_dm_click_falls_back_to_fetching_the_member` gets `_ERR_NOT_MEMBER` (no fetch fallback yet), and the two `Forbidden` tests raise `discord.Forbidden` out of `handle_click`.

- [ ] **Step 3: Add member fetching and error handling**

In `cogs/event_panel.py`, add the import of `status_embed` at the top:

```python
import discord

from modules.config import Config
from modules.embeds import status_embed
```

Add a member-resolution helper to `EventPanelView`:

```python
    async def _member(self, guild, user_id):
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.HTTPException:
            return None
```

Replace the body of `handle_click` from the member lookup onwards with:

```python
        member = await self._member(guild, interaction.user.id)
        if member is None:
            await interaction.response.send_message(_ERR_NOT_MEMBER, ephemeral=True)
            return

        had_role = role in member.roles
        try:
            if had_role:
                await member.remove_roles(role, atomic=True, reason=_AUDIT_REASON)
            else:
                await member.add_roles(role, atomic=True, reason=_AUDIT_REASON)
        except discord.HTTPException as e:
            # Forbidden subclasses HTTPException: the usual cause is the
            # Eventos role sitting above Poliswag's own role.
            await self._report_failure(interaction, member, had_role, e)
            return

        await interaction.response.send_message(
            _LEFT if had_role else _JOINED, ephemeral=True
        )

    async def _report_failure(self, interaction, member, had_role, error):
        action = "remover" if had_role else "dar"
        self.poliswag.utility.log_to_file(
            f"[EVENTPANEL] Failed to {action} role for {member} ({member.id}): {error}",
            "ERROR",
        )
        await interaction.response.send_message(_ERR_FAILED, ephemeral=True)
        mod_channel = self.poliswag.MOD_CHANNEL
        if mod_channel is None:
            return
        embed = status_embed(
            "⚠️ Painel de eventos falhou",
            f"Não consegui {action} o cargo **Eventos** a {member}.\n"
            f"`{error}`\n\nVerifica se o cargo está abaixo do cargo do Poliswag.",
            color=0xE74C3C,
        )
        await self.poliswag.utility.send_embed_to_channel(mod_channel, embed)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/cogs/test_event_panel.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add cogs/event_panel.py tests/cogs/test_event_panel.py
git commit -F - <<'EOF'
Never leave someone staring at "interaction failed"

Every failure path now answers the click: a missing role, someone the
cache has not seen, and the role outranking the bot each get a reason,
and the last one wakes the mods.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Bf8ALc6e3hTDkSFw9QeK6n
EOF
```

---

### Task 3: `!eventpanel` posts the panel, with a pre-flight check

**Files:**
- Modify: `cogs/event_panel.py`
- Test: `tests/cogs/test_event_panel.py`

- [ ] **Step 1: Write the failing tests**

Add the cog import at the top of the test file, next to the existing one:

```python
from cogs.event_panel import EventPanel, EventPanelView, setup
```

Append to `tests/cogs/test_event_panel.py`:

```python
def _ctx(author_id="111"):
    ctx = MagicMock()
    ctx.author = MagicMock()
    ctx.author.id = author_id
    ctx.author.send = AsyncMock()
    ctx.send = AsyncMock()
    return ctx


class TestPostCommand:
    async def test_posts_the_panel_with_a_working_view(self, poliswag):
        guild = _guild(role=_role(position=1), member=_member(), bot_top_position=10)
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)

        await cog.eventpanel(cog, _ctx())

        poliswag.EVENT_PANEL_CHANNEL.send.assert_awaited_once()
        kwargs = poliswag.EVENT_PANEL_CHANNEL.send.await_args.kwargs
        assert isinstance(kwargs["view"], EventPanelView)
        assert isinstance(kwargs["embed"], discord.Embed)

    async def test_refuses_when_the_role_outranks_the_bot(self, poliswag):
        guild = _guild(role=_role(position=20), member=_member(), bot_top_position=10)
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel(cog, ctx)

        poliswag.EVENT_PANEL_CHANNEL.send.assert_not_awaited()
        ctx.send.assert_awaited_once()

    async def test_refuses_when_the_role_is_missing(self, poliswag):
        poliswag.EVENT_PANEL_CHANNEL.guild = _guild(role=None)
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel(cog, ctx)

        poliswag.EVENT_PANEL_CHANNEL.send.assert_not_awaited()
        ctx.send.assert_awaited_once()

    async def test_refuses_when_the_panel_channel_is_unset(self, poliswag):
        poliswag.EVENT_PANEL_CHANNEL = None
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel(cog, ctx)

        ctx.send.assert_awaited_once()

    def test_only_admins_may_run_it(self, poliswag):
        cog = EventPanel(poliswag)
        assert cog.cog_check(_ctx(author_id="111")) is True
        assert cog.cog_check(_ctx(author_id="222")) is False


class TestSetup:
    async def test_setup_adds_the_cog(self, poliswag):
        poliswag.add_cog = AsyncMock()
        await setup(poliswag)
        poliswag.add_cog.assert_awaited_once()
```

Note: `cog.eventpanel` is a `commands.Group` object, so it is called as `cog.eventpanel(cog, ctx)` — the command's callback is not bound to the instance.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/cogs/test_event_panel.py::TestPostCommand -v`
Expected: `ImportError: cannot import name 'EventPanel' from 'cogs.event_panel'`.

- [ ] **Step 3: Write the cog**

Add to the top of `cogs/event_panel.py`:

```python
import discord
from discord.ext import commands

from modules.config import Config
from modules.embeds import status_embed
```

Append to `cogs/event_panel.py`, after `EventPanelView`:

```python
class EventPanel(commands.Cog):
    def __init__(self, poliswag):
        self.poliswag = poliswag

    async def cog_load(self):
        print(f"{self.__class__.__name__} loaded!")

    async def cog_unload(self):
        print(f"{self.__class__.__name__} unloaded!")

    def cog_check(self, ctx):
        return str(ctx.author.id) in self.poliswag.ADMIN_USERS_IDS

    def _preflight(self):
        """Returns (role, error). A role that outranks Poliswag is the
        failure that would otherwise surface much later, as a Forbidden
        for every single member who clicks."""
        channel = self.poliswag.EVENT_PANEL_CHANNEL
        if channel is None:
            return None, (
                "EVENT_PANEL_CHANNEL_ID não está definido ou o canal não foi "
                "encontrado."
            )
        role = channel.guild.get_role(Config.EVENTS_ROLE_ID)
        if role is None:
            return None, f"Não existe nenhum cargo com o id `{Config.EVENTS_ROLE_ID}`."
        if role.position >= channel.guild.me.top_role.position:
            return None, (
                f"O cargo **{role.name}** está acima (ou ao nível) do cargo do "
                "Poliswag, por isso o bot não o consegue atribuir. Arrasta-o "
                "para baixo nas definições de cargos."
            )
        return role, None

    @commands.group(
        name="eventpanel",
        invoke_without_command=True,
        brief="Publica o painel de auto-atribuição do cargo Eventos",
        help=(
            "Publica no canal de anúncios uma mensagem com um botão que dá "
            "(ou tira) o cargo **Eventos**, o cargo que abre o canal do "
            "evento actual. Cada vez que corres o comando é publicada uma "
            "mensagem nova; o texto está em `cogs/event_panel.py`.\n\n"
            "`!eventpanel test` — envia-te o painel por DM para experimentares\n"
            "`!eventpanel clear` — tira o cargo a toda a gente"
        ),
    )
    async def eventpanel(self, ctx):
        role, error = self._preflight()
        if error:
            await ctx.send(embed=status_embed("❌ Não publiquei o painel", error))
            return
        await self.poliswag.EVENT_PANEL_CHANNEL.send(
            embed=status_embed(_PANEL_TITLE, _PANEL_BODY),
            view=EventPanelView(self.poliswag),
        )
        await ctx.send(
            embed=status_embed(
                "✅ Painel publicado",
                f"O botão dá o cargo **{role.name}** em "
                f"{self.poliswag.EVENT_PANEL_CHANNEL.mention}.",
            )
        )


async def setup(poliswag):
    await poliswag.add_cog(EventPanel(poliswag))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/cogs/test_event_panel.py -v`
Expected: 16 passed.

- [ ] **Step 5: Commit**

```bash
git add cogs/event_panel.py tests/cogs/test_event_panel.py
git commit -F - <<'EOF'
Publish the panel, but refuse when the cargo outranks the bot

Posting a panel whose role the bot cannot grant fails once per member
who clicks it, hours later. Check it once, up front.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Bf8ALc6e3hTDkSFw9QeK6n
EOF
```

---

### Task 4: `!eventpanel test` DMs a working panel

**Files:**
- Modify: `cogs/event_panel.py`
- Test: `tests/cogs/test_event_panel.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/cogs/test_event_panel.py`:

```python
class TestTestCommand:
    async def test_dms_the_panel_to_the_caller(self, poliswag):
        guild = _guild(role=_role(position=1), member=_member(), bot_top_position=10)
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel_test(cog, ctx)

        ctx.author.send.assert_awaited_once()
        kwargs = ctx.author.send.await_args.kwargs
        assert isinstance(kwargs["view"], EventPanelView)
        poliswag.EVENT_PANEL_CHANNEL.send.assert_not_awaited()

    async def test_reports_when_dms_are_closed(self, poliswag):
        guild = _guild(role=_role(position=1), member=_member(), bot_top_position=10)
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()
        ctx.author.send = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), "DMs closed")
        )

        await cog.eventpanel_test(cog, ctx)

        ctx.send.assert_awaited_once()

    async def test_still_runs_the_preflight(self, poliswag):
        poliswag.EVENT_PANEL_CHANNEL.guild = _guild(role=None)
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel_test(cog, ctx)

        ctx.author.send.assert_not_awaited()
        ctx.send.assert_awaited_once()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/cogs/test_event_panel.py::TestTestCommand -v`
Expected: FAIL — `AttributeError: 'EventPanel' object has no attribute 'eventpanel_test'`.

- [ ] **Step 3: Implement the subcommand**

Append inside the `EventPanel` class, after `eventpanel`:

```python
    @eventpanel.command(
        name="test",
        brief="Envia-te o painel por DM",
        help=(
            "Envia-te o painel por mensagem privada para veres como fica. O "
            "botão funciona a sério: carrega nele e o cargo é mesmo "
            "atribuído no servidor."
        ),
    )
    async def eventpanel_test(self, ctx):
        _role_obj, error = self._preflight()
        if error:
            await ctx.send(embed=status_embed("❌ Não enviei o painel", error))
            return
        try:
            await ctx.author.send(
                embed=status_embed(_PANEL_TITLE, _PANEL_BODY),
                view=EventPanelView(self.poliswag),
            )
        except discord.Forbidden:
            await ctx.send(
                embed=status_embed(
                    "❌ Não te consigo enviar DM",
                    "Abre as mensagens privadas do servidor e tenta outra vez.",
                )
            )
            return
        await ctx.send(
            embed=status_embed(
                "📨 Enviado por DM",
                "O botão funciona a sério — o cargo é mesmo atribuído.",
            )
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/cogs/test_event_panel.py -v`
Expected: 19 passed.

- [ ] **Step 5: Commit**

```bash
git add cogs/event_panel.py tests/cogs/test_event_panel.py
git commit -F - <<'EOF'
Let the panel be tried in a DM before the whole server sees it

The button in the DM is the real one -- it resolves the clicker back to
the guild -- so the rehearsal covers the grant, not just the wording.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Bf8ALc6e3hTDkSFw9QeK6n
EOF
```

---

### Task 5: `!eventpanel clear` strips the role from everyone

Two-step on purpose: the first call only counts, the second does the work.

**Files:**
- Modify: `cogs/event_panel.py`
- Test: `tests/cogs/test_event_panel.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/cogs/test_event_panel.py`:

```python
class TestClearCommand:
    async def test_without_confirm_it_only_counts(self, poliswag):
        role = _role(position=1)
        holder = _member(roles=[role])
        guild = _guild(role=role, member=holder, bot_top_position=10)
        guild.members = [holder, _member()]
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel_clear(cog, ctx, None)

        holder.remove_roles.assert_not_awaited()
        ctx.send.assert_awaited_once()

    async def test_confirm_removes_the_role_from_every_holder(self, poliswag):
        role = _role(position=1)
        first = _member(roles=[role])
        second = _member(roles=[role])
        guild = _guild(role=role, member=first, bot_top_position=10)
        guild.members = [first, second, _member()]
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)

        await cog.eventpanel_clear(cog, _ctx(), "confirm")

        first.remove_roles.assert_awaited_once()
        second.remove_roles.assert_awaited_once()

    async def test_one_failing_member_does_not_abort_the_sweep(self, poliswag):
        role = _role(position=1)
        broken = _member(roles=[role])
        broken.remove_roles = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), "nope")
        )
        ok = _member(roles=[role])
        guild = _guild(role=role, member=broken, bot_top_position=10)
        guild.members = [broken, ok]
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel_clear(cog, ctx, "confirm")

        ok.remove_roles.assert_awaited_once()
        assert poliswag.utility.log_to_file.call_args.args[1] == "ERROR"
        ctx.send.assert_awaited_once()

    async def test_nobody_to_clear_says_so(self, poliswag):
        role = _role(position=1)
        guild = _guild(role=role, member=_member(), bot_top_position=10)
        guild.members = [_member()]
        poliswag.EVENT_PANEL_CHANNEL.guild = guild
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel_clear(cog, ctx, "confirm")

        ctx.send.assert_awaited_once()

    async def test_still_runs_the_preflight(self, poliswag):
        poliswag.EVENT_PANEL_CHANNEL.guild = _guild(role=None)
        cog = EventPanel(poliswag)
        ctx = _ctx()

        await cog.eventpanel_clear(cog, ctx, "confirm")

        ctx.send.assert_awaited_once()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/cogs/test_event_panel.py::TestClearCommand -v`
Expected: FAIL — `AttributeError: 'EventPanel' object has no attribute 'eventpanel_clear'`.

- [ ] **Step 3: Implement the subcommand**

Append inside the `EventPanel` class, after `eventpanel_test`:

```python
    @eventpanel.command(
        name="clear",
        brief="Tira o cargo Eventos a toda a gente",
        help=(
            "Remove o cargo **Eventos** a todos os membros que o têm, para "
            "limpar a lista no fim de um evento. Sem argumento só conta "
            "quantas pessoas seriam afectadas; `!eventpanel clear confirm` "
            "é que remove mesmo."
        ),
    )
    async def eventpanel_clear(self, ctx, confirm: str | None = None):
        role, error = self._preflight()
        if error:
            await ctx.send(embed=status_embed("❌ Não limpei nada", error))
            return

        guild = self.poliswag.EVENT_PANEL_CHANNEL.guild
        holders = [m for m in guild.members if role in m.roles]
        if not holders:
            await ctx.send(
                embed=status_embed(
                    "Nada a limpar", f"Ninguém tem o cargo **{role.name}**."
                )
            )
            return

        if confirm != "confirm":
            await ctx.send(
                embed=status_embed(
                    "⚠️ Confirmação necessária",
                    f"**{len(holders)}** pessoas têm o cargo **{role.name}**.\n"
                    "Corre `!eventpanel clear confirm` para lhes tirar o cargo.",
                )
            )
            return

        removed = 0
        failed = 0
        for member in holders:
            try:
                await member.remove_roles(role, atomic=True, reason=_CLEAR_REASON)
                removed += 1
            except discord.HTTPException as e:
                # One awkward member must not abort the rest of the sweep.
                failed += 1
                self.poliswag.utility.log_to_file(
                    f"[EVENTPANEL] Failed to clear role from {member} "
                    f"({member.id}): {e}",
                    "ERROR",
                )

        description = f"Cargo **{role.name}** removido a **{removed}** pessoas."
        if failed:
            description += f"\n⚠️ Falhou em **{failed}** (ver logs)."
        await ctx.send(embed=status_embed("🧹 Lista limpa", description))
```

`holders` reads the panel channel's `guild.members` — the same guild object the pre-flight resolved the role from. Do **not** use `role.guild`: under test the role is a mock and `role.guild.members` is a `MagicMock`, not an iterable.

Add the audit-log reason next to `_AUDIT_REASON` at the top of the file:

```python
_CLEAR_REASON = "Limpeza do cargo Eventos via !eventpanel clear"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/cogs/test_event_panel.py -v`
Expected: 24 passed.

- [ ] **Step 5: Commit**

```bash
git add cogs/event_panel.py tests/cogs/test_event_panel.py
git commit -F - <<'EOF'
Empty the Eventos cargo after an event, without deleting it

Discord has no bulk removal, and the usual workaround -- delete the role
and make a new one -- takes every channel overwrite down with it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Bf8ALc6e3hTDkSFw9QeK6n
EOF
```

---

### Task 6: Wire it into the bot

Without `add_view`, every panel from a past event goes dead on the next restart.

**Files:**
- Modify: `main.py`
- Test: `tests/cogs/test_event_panel.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/cogs/test_event_panel.py`:

```python
class TestWiring:
    def test_main_loads_the_cog_and_registers_the_view(self):
        """Without add_view, buttons on panels from past events stop
        working the moment the bot restarts."""
        source = open("main.py").read()
        assert 'await self.load_extension("cogs.event_panel")' in source
        assert "self.add_view(EventPanelView(self))" in source
        assert '"EVENT_PANEL_CHANNEL": Config.EVENT_PANEL_CHANNEL_ID' in source
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/cogs/test_event_panel.py::TestWiring -v`
Expected: FAIL on the first assertion.

- [ ] **Step 3: Wire it up**

In `main.py`, add the import next to the other cog-level imports (after `from modules.role_manager import RoleManager`):

```python
from cogs.event_panel import EventPanelView
```

In `Poliswag.__init__`, after `self.TRAP_CHANNEL = None`:

```python
        self.EVENT_PANEL_CHANNEL = None
```

In `setup_hook`, after the `cogs.trades` line and **before** `await self.tree.sync()`:

```python
        await self.load_extension("cogs.event_panel")
        # Re-registers the persistent view so the buttons on panels posted
        # for previous events keep working across restarts.
        self.add_view(EventPanelView(self))
```

In `get_channels`, add to the `channels` dict after the `TRAP_CHANNEL` entry:

```python
            "EVENT_PANEL_CHANNEL": Config.EVENT_PANEL_CHANNEL_ID,
```

- [ ] **Step 4: Run the whole suite**

Run: `pytest tests/cogs/test_event_panel.py -v && pytest`
Expected: 25 passed in the first, and no new failures in the second.

- [ ] **Step 5: Lint**

Run: `black cogs/event_panel.py main.py modules/config.py tests/cogs/test_event_panel.py && ruff check cogs/event_panel.py main.py tests/cogs/test_event_panel.py`
Expected: black reformats nothing of substance; ruff reports no errors.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/cogs/test_event_panel.py
git commit -F - <<'EOF'
Keep old event panels alive across restarts

A persistent view has to be re-registered at boot, or every button the
bot has ever posted goes dead the next time it restarts.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Bf8ALc6e3hTDkSFw9QeK6n
EOF
```

---

### Task 7: Document it

**Files:**
- Modify: `docs/context.md`

- [ ] **Step 1: Add the cog to the cog map**

In the `## Cog map (cogs/)` table, add a row after the `lures.py` row:

```markdown
| `event_panel.py` | `!eventpanel`, `!eventpanel test`, `!eventpanel clear [confirm]` | admin-only (`cog_check`) |
```

- [ ] **Step 2: Add a module-map note**

In the `## Module map (modules/)` table, replace the `role_manager.py` row with:

```markdown
| `role_manager.py` | Handles Discord role button interactions for the **legacy** team/notification panel (by role *name*, and it auto-grants all `Alertas*` roles to brand-new members). The events panel deliberately does not use it — see `cogs/event_panel.py`. |
```

- [ ] **Step 3: Add the env vars**

In the `## Config env vars (key ones)` block, after the `VOICE_CHANNEL_LEIRIA_ID` line:

```
EVENT_PANEL_CHANNEL_ID  (announcements channel holding the !eventpanel message)
EVENTS_ROLE_ID          (permanent Eventos role the panel button grants)
```

- [ ] **Step 4: Commit**

```bash
git add docs/context.md
git commit -F - <<'EOF'
Write down where the events panel lives

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Bf8ALc6e3hTDkSFw9QeK6n
EOF
```

---

## Deployment (manual, after the plan is done)

Not part of the implementation — these are the human steps from the spec's *Rollout*.

1. In Discord: create the **Eventos** role, drag it **below** Poliswag's role.
2. On the event channel: `@everyone → View Channel ❌`, `Eventos → View Channel ✅`.
3. In `/root/Poliswag/.env`:
   ```
   EVENT_PANEL_CHANNEL_ID=378108892816867329
   EVENTS_ROLE_ID=<the new role's id>
   ```
   The spec records `1551535208979894303` for the role; confirm it before deploying.
4. `cd /root/Poliswag && docker compose -f docker-compose.prod.yaml up -d --force-recreate poliswag` — `env_file` is only read at container create, so a plain restart keeps the stale env.
5. `!eventpanel test` in any channel, click the button in the DM, confirm the role appears and toggles off.
6. `!eventpanel` to publish it for real.
