# Cog map (`cogs/`)

Full command table. The core `docs/context.md` keeps only the auth summary.

| Cog | Commands | Auth |
|-----|----------|------|
| `quests.py` | `!quest <area> <search>` (active registry areas), legacy `!questleiria` / `!questmarinha`, `!scan`, `!exportquests` | `!scan`+`!exportquests` admin-only |
| `tracker.py` | `!track`, `!untrack`, `!tracklist`, `!untrackall`, `!tracked` | admin-only (`cog_check`) |
| `event.py` | `!eventtypes`, `!exclude`, `!include`, `!excludedlist`, `!excludeclear` | admin-only |
| `accounts.py` | `!accounts` | `MY_ID` only (`cog_check`) |
| `moderation.py` | Listeners: `on_interaction` (role buttons), `on_message_delete`, trap channel (see `moderation.md`) | — |
| `scheduled.py` | `!weeklydigest`, `!testevent HH:MM`, `!ticks` (every scheduler step: last duration, age, failures); four one-minute loops, health / alerts / display / chores, 22 steps in all (list and rules in `conventions.md` → Scheduler; the daily "erro(s) novo(s)" digest is DMed to `MY_ID` since 2026-09-26) | admin-only |
| `webstats.py` | `!stats` / `!webstats`, `!stats export [7d\|30d\|Nd\|all]`, `!stats novocodigo` — answers by DM | `MY_ID` only (`cog_check`) |
| `pokedex.py` | `!pokedex` / `!trades` / `!trocas` (DM login code), `!resumo [dias]` (trade digest preview) | Members; DM-capable (own membership check) |
| `event_panel.py` | `!eventpanel` (publica o painel), `!eventpanel test` (DM de ensaio), `!eventpanel clear [confirm]` | `MY_ID` only (`cog_check`) |
| `announcements.py` | `!anunciar <texto>` — reposts the text (+ attachments) as typed in the announcements channel (`EVENT_PANEL_CHANNEL_ID`). Echoes a pings-off preview first; posts only on **Publicar** (author only, 5 min, one press), with `AllowedMentions.all()` so a typed `@everyone` pings. Deletes the command message once the preview is up (kept on errors); attachments are read into memory first so Publicar still has them | admin-only (`cog_check`) |
| `giveaways.py` | `!sorteio` — sorteio por reação num anúncio, revisto pelos mods. Registar: responder ao anúncio com `!sorteio` ou `!sorteio <link> [<t:…>] [n]`; hora (`<t:…>`) e emoji personalizado lidos do texto, o bot reage com ele. `campfire` + capturas (anexo, resposta ou link): os nomes vão ao Claude (`modules/campfire.py`, visão, JSON estruturado) e ficam em `campfire_names`; entram no sorteio, e um nome que corresponda a quem reagiu (nome no Discord ou treinador de `shiny_link`) conta uma vez; `campfire limpar`. Tick de 60 s (ou `sortear`) tira o nome (`secrets`) entre reações (sem bots, sem o autor do anúncio, que entrega o prémio, nem quem registou, só membros) e Campfire, e **não anuncia**: põe uma revisão no `MOD_CHANNEL`, sem prazo, com Publicar · Repetir · Repetir sem este · Cancelar (`custom_id` `sorteio:<ação>:<id>`, tratados em `on_interaction`, sobrevivem a reinícios). Publicar só pelo autor do anúncio (mods se o autor é um bot) e responde ao anúncio (só pinga o vencedor); Repetir não exclui (evita só quem estava a sair); Repetir sem este guarda-o em `excluded_ids`; botões só valem na revisão atual (`review_message_id`) e sobre o nome mostrado. `reroll`/`repetir` depois de publicado substitui um vencedor (o único, ou `@pessoa`/nome do Campfire): nova revisão, e ao publicar o substituído passa a `excluded_ids` e a resposta pública anterior (`announcement_reply_id`, 044) é apagada e refeita, por isso o canal nunca mostra o reroll; `lista` (todos os nomes, Discord e Campfire, sem pings; anexo `.txt` se for longo), `estado`, `cancelar`. Tabela `giveaway` (042, 043, 044) | mods (`cog_check`); Publicar: autor do anúncio |
| `shiny.py` | `!shiny <código>` / `!shiny` / `!shiny remover`; listener for the alert verdict buttons | `!shiny`: `MY_ID` only during the pilot (`owner_only`), built for members, works in DMs (own membership check) |
| `shiny_access_requests.py` | Listener for the shiny-map **Aceitar**/**Rejeitar** buttons on the moderation-channel embeds | `MY_ID` or `ADMIN_USERS_IDS`, only in `MOD_CHANNEL_ID` |
| `spawn_history.py` | `!spawnhistory`, `!spawnhistory export [hours]`, `!spawnhistory leaderboard [hours]`, `!spawnhistory image`; independent 30-second collector | `MY_ID` only |
| `support.py` | `!mbway` — dados de apoio por MB Way | Público; só responde por DM (em canal apaga o comando). O texto vem de `MBWAY_DETAILS` |
| `mentions.py` | Owner mentions delegate free-form requests privately; other tagged members get public answers from **Hermiswag** (separate sandboxed Hermes on this host, Sonnet 5.5; channel conversation in convívio/chat-raids/suporte-tecnico, 6-hourly learning from the server), "lista de …" CSV dumps, fallback to the agent worker | Actual Discord identity; owner agents read-only; public path sees only validated public data; cooldowns. See `agents.md` → Hermiswag |

## Removed on 2026-11-11

Four cogs went after seven weeks without a logged use (the action log from 2026-09-24): `container_manager.py` (`!container`, `!status`, `!device`), `notifications.py` (`!notify`), `lures.py` (`!lures`, `!uselure`, `!lure`, `!pstoken`) and `agents.py` (`!self`, `!jobs`, `!job`, `!cancel`, tagged Claude/Codex jobs in owner DMs). What replaces them:

- Scanner containers and the phone: the recovery ladder acts by itself; by hand, from the host. The two switches are columns of the `poliswag` table, read again every five minutes: `UPDATE poliswag SET auto_recreate_enabled = 0` stops the ladder's restarts, `auto_reboot_enabled = 0` stops the phone-offline alerts.
- Poracle channels and rules: Poracle's own database and API (`!notify` had not changed a rule since the log began).
- Owner requests: the private Hermes / Claude DM conversation. The host worker (`poliswag-agent.service`) stays, as Hermiswag's fallback for public replies.
- The tables `account_lure`, `lure_lock` and `polisnatch_job` are left in place, unused.
