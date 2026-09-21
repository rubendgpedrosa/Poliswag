# Events role panel — design

**Date:** 2026-09-21
**Status:** Approved for planning
**Touches:** `cogs/event_panel.py` (new), `modules/config.py`, `main.py`, `tests/cogs/test_event_panel.py` (new).
**Leaves alone:** `modules/role_manager.py`, `cogs/moderation.py` — see *Why not the existing path*.

## Goal

A message Poliswag posts in the announcements channel with a button under it. A member clicks it, gets the **Eventos** role, and the role's permission overwrite reveals that event's channel. Clicking again gives the role back.

The role is permanent and shared by every event. A new event is a new channel with the role's overwrite added — no code change, no redeploy, and everyone who already opted in sees it the moment the channel exists.

## Fixed IDs

| Setting | Value | Changes? |
|---|---|---|
| `EVENT_PANEL_CHANNEL_ID` | `378108892816867329` — announcements, where the panel is posted | No |
| `EVENTS_ROLE_ID` | `1551535208979894303` — the Eventos role | No |
| Event channel | `1551534974413443082` for the current event | Per event, **written inline in the panel copy** as `<#…>`, not configured |

The event channel is deliberately not an env var. The panel copy is a constant in `cogs/event_panel.py` that gets edited per event anyway; splitting the channel out into config would mean editing two places to change one sentence.

## Scope

| In | Out (deliberately) |
|---|---|
| One panel, one role, one button | A generic multi-panel system with a DB table |
| A fresh message per `!eventpanel` run | Find-and-edit of a previous panel — a human cannot edit a bot's message, and a new event wants a new announcement anyway |
| Button (`discord.ui.View`) | Emoji reactions — no per-user feedback, needs the message ID persisted, and anyone can pile junk emojis on the panel |
| Toggle on/off, self-serve both ways | Opt-in-only with admin-gated removal |
| Admin bulk-strip of the role | Any automatic expiry or scheduled clearing |
| The bot only adds/removes the role | The bot creating roles or writing channel overwrites |

The role and the channel overwrites are set up by hand in Discord. `@everyone: View Channel ❌`, `Eventos: View Channel ✅`. Poliswag never touches channel permissions; a bug there would silently change who can see what.

## Why not the existing path

`modules/role_manager.py` already toggles roles, and `cogs/moderation.py:on_interaction` already routes button clicks into it. Neither is reused:

- **It is name-based.** `toggle_role` does `discord.utils.get(user.guild.roles, name=role)`. We have an ID, which survives a rename.
- **It has side effects.** For a member holding only `@everyone`, `toggle_role` also calls `_add_default_notif_roles`, granting all five `Alertas*` roles. Someone opting into an event channel should not be silently subscribed to every ping in the server.
- **It cannot answer.** That listener ends in `interaction.response.defer()`. The clicker sees nothing happen. A persistent `discord.ui.View` can reply ephemerally.
- **Its routing is a hardcoded list** of `custom_id`s in `moderation.py`. The new `custom_id` (`event_panel:toggle`) is not in that list, so the two paths cannot double-handle the same click.

The legacy panel keeps working, untouched.

## Components

### `modules/config.py`

Two vars, in the existing `int(os.environ.get(..., "0"))` style:

```python
EVENT_PANEL_CHANNEL_ID = int(os.environ.get("EVENT_PANEL_CHANNEL_ID", "0"))
EVENTS_ROLE_ID = int(os.environ.get("EVENTS_ROLE_ID", "0"))
```

Both go in `.env`. Prod needs `up -d --force-recreate poliswag`; `env_file` is only read at container create.

### `cogs/event_panel.py`

- `_PANEL_TITLE` / `_PANEL_BODY` — Portuguese, matching the rest of the bot. The per-event text lives here.
- `EventPanelView(discord.ui.View)` — `timeout=None`, one button, `custom_id="event_panel:toggle"`.
- `EventPanel(commands.Cog)` — `cog_check` returns `str(ctx.author.id) in self.poliswag.ADMIN_USERS_IDS`, the same guard `lures.py` and `tracker.py` use.

### `main.py`

`await self.load_extension("cogs.event_panel")` in `setup_hook`, plus `self.add_view(EventPanelView(self))`. Registering the view is what makes panels from previous events keep working after a restart — without it, every old button goes dead on deploy.

## Commands

### `!eventpanel test`

DMs the panel to the invoker. The button genuinely works there: in a DM `interaction.guild` is `None` and `interaction.user` is a `User`, not a `Member`, so the callback falls back to the guild behind `EVENT_PANEL_CHANNEL_ID` and resolves the member with `guild.get_member(interaction.user.id)`. The DM therefore exercises the real grant path, not just the rendering.

A member who cannot be resolved in that guild gets an ephemeral "não te encontro no servidor" rather than a silent no-op.

### `!eventpanel`

Posts a fresh panel to `EVENT_PANEL_CHANNEL_ID`. Pre-flight, before posting:

1. `EVENTS_ROLE_ID` resolves to a role in that guild.
2. That role is **below** Poliswag's top role.

Either check failing aborts with an explicit message naming the problem. This is the failure that otherwise appears much later as a `Forbidden` for every member who clicks — cheap to catch once, expensive to debug in the wild.

### `!eventpanel clear`

Two-step, because it is irreversible for the people it affects:

- `!eventpanel clear` — counts the members holding the role and replies with the number and how to confirm.
- `!eventpanel clear confirm` — removes the role from each of them, reporting the number removed and the number that failed.

Individual `Forbidden`/`HTTPException` failures are counted and logged, not raised — one problem member must not abort the rest of the sweep. Discord has no bulk role removal; the usual workaround is deleting and recreating the role, which destroys every channel overwrite it appears in.

## Click behaviour

```
member has role  → remove → "❌ Saíste dos eventos."
member lacks role → add    → "✅ Tens agora acesso aos canais de eventos."
```

Both replies are `ephemeral=True`, so the announcements channel stays clean no matter how many people click.

## Error handling

| Case | Member sees | Operator sees |
|---|---|---|
| Role deleted / `EVENTS_ROLE_ID` wrong | Ephemeral "configuração inválida, avisa um admin" | `log_to_file(..., "ERROR")` |
| `Forbidden` (role outranks bot, or Manage Roles missing) | Ephemeral apology | `ERROR` log + notice to `MOD_CHANNEL` |
| Member unresolvable (DM test) | Ephemeral "não te encontro no servidor" | — |
| `HTTPException` mid-`clear` | — | Counted in the reply, logged per failure |

No path leaves the interaction unanswered; an unanswered interaction shows the user a red "interaction failed".

## Tests

`tests/cogs/test_event_panel.py`, pytest + `mocker`, following the existing cog tests:

- Click without the role → `add_roles` called with the resolved role; ephemeral confirms the grant.
- Click with the role → `remove_roles` called; ephemeral confirms removal.
- DM click (`interaction.guild is None`) → member resolved via the panel channel's guild, role still granted.
- DM click by a non-member → ephemeral error, no role call.
- Role missing → ephemeral error, `ERROR` logged, no role call.
- `Forbidden` on `add_roles` → ephemeral apology, `MOD_CHANNEL` notified.
- `!eventpanel` with the role above the bot's top role → refuses, posts nothing.
- `!eventpanel clear` → counts only; `clear confirm` → removes from every holder and survives one member raising `Forbidden`.

## Rollout

1. Create the **Eventos** role by hand; confirm it sits below Poliswag's role.
2. Set the event channel to `@everyone: View ❌`, `Eventos: View ✅`.
3. Add both vars to `.env`, `up -d --force-recreate poliswag`.
4. `!eventpanel test` — click it in the DM, confirm the role lands and toggles.
5. `!eventpanel` in announcements.
