# Events role panel (`cogs/event_panel.py`)

### Events role panel (`cogs/event_panel.py`)

Self-serve opt-in: a button in the announcements channel grants/removes the permanent **Eventos** role, and a channel permission overwrite turns that role into access to the current event channel.

- A new event = a new channel with the role's overwrite; everyone already opted in sees it immediately. Only `_PANEL_TEXT` is edited (the event's name), then `!eventpanel` re-run — a redeploy is needed for that edit to reach the container.
- The panel is a plain message plus the button, not an embed, and **carries its own `@everyone`** — the panel *is* the announcement. `allowed_mentions` is set explicitly (`everyone=True` on the post, `AllowedMentions.none()` on the DM rehearsal, so a rehearsal can never ping). **Every `!eventpanel` run pings the whole server**, including a re-run to fix a typo.
- `_PANEL_TEXT` ships with a `<NOME DO EVENTO>` placeholder; `!eventpanel` refuses to post while it is still there, because that post would `@everyone` the server with the raw template. `!eventpanel test` deliberately still sends it.
- It names no channel on purpose: its audience cannot see the channel yet, and Discord renders a hidden channel's mention as a dead link for them.
- `EventPanelView` is persistent (`timeout=None`, `custom_id="event_panel:toggle"`), re-registered by `main.py`'s `setup_hook` via `add_view` — without that, buttons on panels from past events die on the next restart. It must stay in `setup_hook`: `discord.ui.View.__init__` needs a running loop, so `__init__` would raise `RuntimeError` at boot.
- The role is resolved **by id**, and the clicker is resolved through the panel channel's guild — so the button behaves identically in a DM, which is what makes `!eventpanel test` a real rehearsal rather than a preview.
- `_preflight()` → `(role, channel, error)` refuses to publish when the channel is unset, the role is missing, or the role's position is `>=` Poliswag's top role. That last one would otherwise surface as a `Forbidden` once per member who clicks, hours later.
- Does **not** reuse `modules/role_manager.py`: that one is name-based, grants every `Alertas*` role to a brand-new member, and `defer()`s without replying.
- Role and channel overwrites are set up by hand in Discord; the bot only adds/removes the role, never touches channel permissions.
