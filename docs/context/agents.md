# Discord delegation to Claude and Codex

The owner's private DM chat uses **Hermes in CT 109**, through `cogs/hermes.py`
and `modules/hermes_client.py`. Send plain text in a DM, or visibly tag Poliswag
in that DM. All guild mentions, including the owner's and explicit provider
requests, use the restricted public path below; they never access Hermes or
private agent jobs. Guild messages without a visible bot tag do not trigger
chat. `!` commands retain command dispatch. A message starting with `!ignore` (any author, DM or guild) is dropped before anything else: no Hermes turn, no tag reply, no command — use it for notes the bot must not act on. Bots, webhooks, forged author
objects and nonowners cannot call Hermes. The Hermes entry point also rejects
all guild contexts, independently of mention routing.

Owner DMs share one durable server-side conversation:
`poliswag:owner:<MY_ID>`. Hermes owns its model login, tools, memory and history
in CT 109. Poliswag sends only the owner's current DM text and fixed instructions,
never attachments, channel history, another user's messages, scanner context,
Discord/provider credentials or production configuration. Replies to this
conversation stay in the owner's DM; new guild messages never enter its history.

`HERMES_API_URL` defaults to `http://192.168.1.60:8642` (check the DHCP address).
`HERMES_API_KEY` is required for requests and defaults empty, disabling calls.
The bearer key is a Hermes API credential, not a model/provider token. The
client admits durable work with `POST /v1/runs`, `Idempotency-Key:
discord:<message-id>`, and `X-Hermes-Session-Key: poliswag:owner:<MY_ID>`.
`HERMES_SESSION_ID` optionally selects the existing Hermes session explicitly
(required when migrating the earlier Responses conversation). The payload is
`input`, fixed sysadmin instructions, and optional `session_id`; it never sends
`conversation`, model login, attachments, or channel history. Polling
`GET /v1/runs/<run-id>` returns only the final `output` string. Partial or
interrupted results are labelled clearly and never presented as success.
A transient admission disconnect retries once with the identical idempotency
key/payload; polling retries transient disconnects without submitting work again.
HTTP operations have short timeouts (60 seconds POST, 30 seconds GET), while a run has a two-hour polling budget. Failure or timeout warns that work may still
be running, rather than encouraging an automatic repeat.

Hermes acts as the owner's sysadmin across the host and guests: a concrete
request authorizes routine diagnosis, code/configuration repairs, testing and
verification in its scope. It reads the target project's AGENTS.md first, keeps
credentials private and never reboots the Proxmox host. Genuinely necessary
approvals come only from the authenticated Runs status `approval.request`, not
model-generated reply text. The owner receives a private embed with action/
command details (possible credentials hidden) and **Aprovar uma vez** / **Recusar**
buttons. Only the configured owner's real Discord user in that exact DM can
respond. Buttons bind the run/request IDs server-side; no permanent approval
option exists. Timeout (275 seconds), callback failures and invalid choices deny
the request. `POST /v1/runs/<run-id>/approval` submits the exact `request_id` and
`choice: once|deny`, then the same run continues. Once decided (or timed out), the approval card is deleted and the decision
is logged on the turn's live status card (🔓 aprovado / 🚫 recusado, plus an approval count in the final summary). Guild mentions, nonowner DMs
and legacy agent jobs keep their existing boundaries.

One FIFO active turn and at most five waiting turns serialize the owner's
conversation. Cancellation frees queue capacity. Missing configuration,
malformed output and transport failures get generic PT-PT replies without
exception details or a fallback to another agent. Owner DM messages get no reactions (2026-11-01); the live card described below is the first reply. The response returns to that origin with mentions disabled,
grouping related paragraphs into messages up to Discord's 2000-character limit.
All replies stay in chat: no automatic text/code attachments. Splits preserve prose
and keep short fenced blocks intact; oversized technical blocks are split at line
boundaries when possible, closing and reopening the same code fence/language in
each message. A single overlong code line is hard-wrapped without dropping content.
Only the first segment replies to the originating Discord message; subsequent
segments are ordinary channel messages with `silent=True` (suppress push
notifications), no reply reference and no mentions. Mentions remain disabled
throughout. Startup recovery uses the same sender, labels the result as recovered,
and sends ordinary channel messages because it lacks the original message context. A failed segment leaves
the run pending; as before, a retry after partial delivery may repeat earlier text. Runs survive gateway transport
interruptions. Poliswag persists a message-to-run map in `HERMES_RUNS_DIR`
(default `logs/hermes-runs`) when a run is admitted: one record per run,
holding the run ID and the destination DM channel, removed only after the
reply is delivered. On startup the Hermes cog re-polls those pending runs
without re-admitting them — the original Discord message ID stays the
idempotency key, so the work is never run twice — and delivers terminal
results to the stored DM. A record left after a failed delivery is kept for
the next startup; records older than three hours are dropped. A stale approval encountered after a restart is denied, so a stuck run still terminates.

### Progress feedback and control

While a turn runs, `HermesClient` also reads `GET /v1/runs/<run-id>/events`
(SSE, resumed with `Last-Event-ID`, best effort) and reports status changes from
polling; polling stays authoritative for approvals and the result, and a broken
stream or display error never affects the run. The cog keeps the DM typing
indicator on and immediately replies with one live status embed (first line
📝 = the request, credential-filtered), edited every four seconds: elapsed time, the last five tool calls
(✓/✗ with duration), the current tool with a short preview, and Hermes' latest
commentary. Previews and commentary pass the same credential filter as approval
cards. On completion the embed collapses to a summary (request, tool count, time,
errors); the answer follows as a reply. Discord does not let a bot delete a user's
messages in a DM, so the owner's messages stay.

**Follow-ups steer (2026-11-01).** A new owner DM sent while that agent's turn is
running is sent straight to `POST /v1/runs/<id>/steer` (`Hermes.steer_active`,
waiting up to 15 s for a just-started turn to be admitted) and shows on the running
card as 💬 guidance; no new card or turn. If the run refuses it (already finished),
it becomes a normal new turn. `+ texto` forces a separate task. An unprefixed DM
while only Claude is busy steers Claude (`main.py`, `handle(..., steer_only=True)`)
and falls back to Hermes if Claude refuses it.

The embed carries owner-only buttons (same identity/DM checks as approvals):
**Parar** calls `POST /v1/runs/<id>/stop`; **Redirecionar** opens a modal whose
text goes to `POST /v1/runs/<id>/steer` for the running agent. A turn queued
behind another shows **Na fila** with **Enviar à tarefa atual**: the queued
request leaves the local queue (only while still queued, never after admission)
and is sent as steer guidance to the active run instead; if the run
refuses it, the request rejoins the queue as a normal turn. When accepted, the queued request's own card is
deleted and the active turn's card shows the text as 💬 guidance; steers are counted once even
when the run also emits `run.steered`.

## Claude DM conversation (`c ` prefix)

An owner DM starting with `c ` or `!c` (any case) goes to **Claude Code in CT 109** instead of
Hermes; the prefix is stripped and a bare `c` gets a usage hint. Everything else in the DM keeps
going to Hermes. `cogs/claude_chat.py` subclasses the Hermes cog, so the same owner/DM checks,
FIFO queue, live status card, **Parar**/**Redirecionar**, segmentation and restart recovery apply
(own records in `CLAUDE_RUNS_DIR`, default `logs/claude-runs`). The routing lives in
`main.py` (`_CLAUDE`), before the Hermes DM block; tags still go to the mention path.

The backend is `claude-bridge.service` on CT 109 (`/root/claude-bridge/bridge.py`), listening on
`CLAUDE_API_URL` (`http://192.168.1.240:8643`) with bearer `CLAUDE_API_KEY` (in `.env`; empty
disables calls). It implements the Runs API subset `HermesClient` uses and runs each turn as
`claude -p --resume` on one persistent session in `/root/discord-claude`, separate from Hermes
and from the always-on Remote Control session. Claude runs without permission prompts, so no
approval cards appear. Tests: `tests/cogs/test_claude_chat.py`.

## Explicit Claude/Codex jobs (removed 2026-11-11)

`cogs/agents.py` took tagged owner requests (`@Poliswag codex …`, `answer <id> …`)
and the commands `!self`, `!jobs`, `!job` and `!cancel`. It ran 22 jobs between
11 and 19 October 2026 and none after the owner moved to the Hermes and Claude
DM conversations, so the cog, its database bridge (`modules/agent_database.py`)
and its delivery rules (`modules/agent_destination.py`) are gone. A tagged
owner DM now goes straight to the Hermes conversation (`cogs/mentions.py`).

The worker's owner-job half went the same day: the per-job Git worktrees, the
`poliswag_db` MCP bridge (`scripts/agent_db_mcp.py`), reply parsing
(`modules/agent_reply.py`), file staging (`modules/agent_artifacts.py`) and the
`waiting_input`/resume protocol. What stays is the host worker
(`scripts/agent_worker.py`, `poliswag-agent.service`) and the queue
(`modules/agent_jobs.py`), because the public path falls back to them when
Hermiswag does not answer (see "Execution and boundaries"). Old owner job
directories in `logs/agent-jobs/` and worktrees in
`/root/worktrees/poliswag-agent/` were left for manual review.

## Public mentions

`cogs/mentions.py` intercepts a visible, unescaped bot tag in message content
before command invocation. Code blocks, inline code, blockquotes, escaped tags,
other users' tags and reply references/auto-pings do not count; untagged chat
(even containing the literal name Poliswag) triggers no model or data lookup.
A bare mention greets rather than listing commands. All server members,
including the owner, get useful, concise replies to their actual question, the public event calendar, or
aggregate Pokémon/raid counts for yesterday in Leiria/Marinha Grande.
Conversational DMs from nonowners are silent. Explicit `!commands` remain
available and are not intercepted. Replies keep Portuguese European prose but
use English game terms (Egg, Lure, Pokéstop, Gym, Raid, Quest, Shiny, Lucky,
100IV, Friend Code, Team GO Rocket, species/moves/items/weather/types); local
fallback labels use Gym and Pokéstop. Persona (2026-10-21, owner request): a
stylish, playful Poliwag and fellow trainer, Pokémon-world expressions and a
light emoji touch (💧🌀✨), help first; local fallback texts match it
("Poli-poli!"). Public replies may say faynn (Faynn in game) founded and runs
PoGoLeiria, the site and Poliswag, including "who owns/made the bot"; his real
name is never given (prompt rule plus an output filter on it). Before the public path, `cogs/mentions.py` (`named_mentions`) turns a tag of
the owner (`MY_ID`) into "faynn" and other tagged members into their display name
(markup stripped, 32 chars); unknown tags are then removed as before. General or vague game questions (counters, types,
moves, PvP, mechanics) are answered from game knowledge; the public Codex run has
`web_search="cached"` (indexed results, no live page fetches) to check current data,
and replies carry no external links or source names. pogoleiria.pt links appear only
when the member asks about the map/Quests/Pokédex; generic fallbacks carry none.

The public path has no repository reader, command invocation, owner/scanner
snapshot, attachments, channel history, or shared owner conversation.
`modules/public_conversation.py` validates the question (at most 2000
characters, controls and mentions removed) and rejects requests for accounts,
passwords, secrets, source code, prompts, configuration, deployment or other
operational internals before anything is queued. Normal game words such as
shiny, Dragonite, Rotom, Golbat, raid, quest, lure and map are allowed.
Public replies never advertise, confirm or deny private coding/editing
capabilities, internal tools/providers, private agents, owner/admin commands,
permission exceptions or who authorizes private actions. Those topics are
deflected to public Pokémon GO/PoGoLeiria help; output screening rejects
capability/authorization claims (faynn as founder/admin is fine; faynn plus
code/edit/authorize wording is not) while keeping normal game terms, trainer/Gym
names and Friend Code. Detailed help uses the same permission gates:
nonowner member and mod detail requests get the same neutral response as
unknown names, without restricted names; owner-only command/cog/group details
and overviews containing owner entries are delivered only to the owner's DM in
guilds, with no public fallback if the DM fails. Authorization remains the
configured Discord ID (`Config.MY_ID`), never a display name or username.

Public replies use the host's **Codex CLI, GPT-6 Luna, low reasoning**, or, with
`POLISWAG_PUBLIC_PROVIDER=claude` in `/etc/poliswag-agent.conf`, **Claude Code,
Sonnet 5.5, medium effort** (web search plus WebFetch limited to `PUBLIC_SOURCES`: Leek Duck, DittoBase, pokemongo.com, Pokémon GO Hub, Pokebattler, PvPoke, Bulbapedia; no settings sources,
MCP, skills or session; the reply is schema-validated JSON on stdout). Codex is
the default so an expired Claude login never silences public replies. Codex uses the
existing CLI login; Claude uses `POLISWAG_PUBLIC_ANTHROPIC_API_KEY` (+ `POLISWAG_PUBLIC_ANTHROPIC_WORKSPACE` for a user-scoped key) from the worker's EnvironmentFile, mapped to `ANTHROPIC_API_KEY` for public runs in place of the host's Claude login. There is no direct Responses API call and no separate API
credential in the bot. `modules/conversation_model.py` submits the sanitized
question plus a strictly validated public map snapshot to the separate
`logs/agent-jobs/public/` queue. The queue prompt field still carries a fixed
topic (greeting/events/statistics/map); the question and snapshot travel in a
validated context object. Public requests use reserved identity `0`. The worker validates owner, provider,
mode, topic, question text, snapshot schema/types/sizes again before execution.

`modules/public_map_context.py` supplies the only map data. It receives the
scanner query interface and a copy of public species names (never the service,
bot or Config). Queries are fixed, read-only, parameterized and limited to a
the union box of every active registry area's fence (`scan_areas.bbox()`: Leiria, Marinha Grande, Batalha, Porto de Mós, Moita):
live encounter spawns (`seen_type='encounter'`, verified expiry, future
`expire_timestamp`), current gym Raids (`deleted=0`, `raid_level>0`, future
`raid_end_timestamp`; Eggs distinguished from active bosses and include a
validated future `hatches_at` when available), live Lures
(`deleted=0`, future `lure_expire_timestamp`), and current normal/alternative
quests (`deleted=0`, appropriate future expiry). Since 2026-11-08 also live Max Battles at Power Spots (`station`: name, coordinates, level, boss, end time; `battle_end`/`end_time` in the future) and a Pokéstop/Gym name lookup for "onde fica…" questions (`LIKE` on up to five question words, area names and question words dropped; name, coordinates and, for a Gym, its team; 10 best matches). Rule (owner, 2026-11-08): what the public pogoleiria.pt map shows may be used; never scanner accounts, devices, workers, proxies, servers, databases, code, internal tools or credentials. The public `INSTRUCTIONS` state the same. Only known public fields are
selected and converted; no `SELECT *`, encounter identifiers, trainer data,
accounts, raw reward JSON or arbitrary SQL. Each section is capped at 20 rows,
uses a five-second total budget and fails independently, so a partial query
marks only that section unavailable. The prompt states that snapshots are
samples, not an exhaustive inventory, and the model must distinguish yesterday's
statistics, current samples, truncation and missing data.

Each public run starts in a fresh temporary directory, uses a replacement
mascot instruction file, ignores user configuration and project rules,
disables shell execution, image reading, web search and multi-agent tools,
and uses a read-only sandbox and ephemeral session. The worker reconstructs the
prompt as trusted instructions plus serialized untrusted member data; prompt
injection cannot add tools, permissions or owner context. Generated text may contain numbers,
accents, line breaks, game terms and public `pogoleiria.pt` links (including
`/mapa/@...`), but must pass length (about 1800 characters), credential,
code/backtick, internal-path, mention and foreign-link checks. Calendar facts
and statistics are rendered locally, passed to the model as a strict
`facts` context field, and still appended authoritatively; the model cannot
rewrite them and a no-data claim is replaced by the local facts. Worker errors
and unsafe output fall back to helpful local replies, never a fake success or a
fixed joke. Maximum two
waiting public calls per bot, 45-second execution timeout, 55-second bot wait, 35-second queue
wait, bounded output and 60-second error backoff. Queued requests expire after
35 seconds; timeout/cancellation marks them cancelled. A busy worker gives an
immediate local fallback. No raw CLI output or error details are retained for
public runs; request/result queue artifacts follow the existing manual retention
policy. Restart recovery interrupts unfinished public jobs without replay.

No accounts, device/worker details, shiny tables, analytics, code, configuration,
environment, credentials or implementation information enters public replies.
Sensitive-topic requests get a fixed helpful deflection, not an explanation.

Additional safeguards: real Discord member/owner identity checks, no webhooks
or bots, 15-second member cooldown, five member requests per guild per 30 seconds,
bounded cooldown memory, five-second data timeout, 65-second total reply limit,
validated calendar titles and numeric values, disabled mentions, and generic
failure replies without exception text. Missing mention handling fails closed
before command dispatch. Neither public prompts nor exception details are
logged by the mention path.

CLI behavior: [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive)
and [configuration reference](https://developers.openai.com/codex/config-reference).

A guild tag that passes the cooldowns is first acknowledged with a 👀 reaction
(`react_to_request`), never a canned message. Mentions are disabled on every reply.

## Execution and boundaries

The host worker only runs the public fallback above. Its queue is
`logs/agent-jobs/public/` (`AGENT_JOBS_DIR` inside the bot, plus `public/`),
shared through the existing `/app`/`logs` bind mounts. `modules/agent_jobs.py`
is the queue (`AgentJobs` submit/get/list/cancel/`worker_status`, atomic
`write_json`), standard library only because the worker imports it outside the
container. A job directory holds `request.json`, `result.json` (state and the
JSON reply) and, when the bot gives up, a `cancel` marker; no CLI output is
kept. The worker writes its heartbeat to `public/worker.json` (`heartbeat`,
`active`, `providers`), which the bot treats as offline after 30 seconds, and
holds `worker.lock` in the queue root so only one worker runs. It takes one job
at a time, oldest first, polling every two seconds.

The CLIs get an environment allowlist without Discord or database credentials
and run in a fresh temporary directory, never in the repository.
Cancellation and timeouts terminate the process group. A restart marks
unfinished jobs `interrupted` without rerunning them.

The supplied unit mounts `/root` as an empty tmpfs and binds back only the live
source (read-only, production `.env` hidden), `~/.local`, the queue and the
CLIs' login and cache folders; `/var/lib/docker`, `/opt`, the shadow files and
the Docker socket are hidden. It keeps SETUID/SETGID/SETFCAP for Codex's
bubblewrap sandbox; mount and ptrace capabilities are excluded and privilege
escalation is disabled. The worker still accepts `--owner-id` (and `--repo`,
`--worktrees`) without using them, so a unit written for the owner jobs starts
it unchanged.

## Host installation

Install and log in the Codex CLI (`codex login status`); the Claude CLI is only
needed with `POLISWAG_PUBLIC_PROVIDER=claude` and then uses
`POLISWAG_PUBLIC_ANTHROPIC_API_KEY`. The bot container needs nothing extra.

```sh
apt-get install -y bubblewrap
mkdir -p /root/Poliswag/logs/agent-jobs /root/.cache
chmod 700 /root/Poliswag/logs/agent-jobs
# The unit passes --owner-id from this file; the worker ignores the value.
printf 'POLISWAG_AGENT_OWNER_ID=YOUR_DISCORD_ID\n' > /etc/poliswag-agent.conf
chmod 600 /etc/poliswag-agent.conf
cp scripts/agent-worker.service /etc/systemd/system/poliswag-agent.service
systemctl daemon-reload
systemctl enable --now poliswag-agent.service
```

Check `systemctl status poliswag-agent` / `journalctl -u poliswag-agent`.
Disable with `systemctl disable --now poliswag-agent`; public tags then get the
local fallback replies when Hermiswag does not answer. Job directories are
never deleted automatically.


## Hermiswag (public agent, since 2026-11-08)

Public tag replies go first to **Hermiswag**, a separate Hermes install on this
host (user `hermiswag`, uid 1990, home `/home/hermiswag`, fresh state with no
link to the owner's Hermes on CT 109). Model: Anthropic **Sonnet 5.5, medium
effort**, using the public-only API key and its workspace
(`providers.anthropic.extra_headers.anthropic-workspace-id`). Its only toolset on
the `api_server` platform is `web` (Exa); memory, user profile, terminal, file,
skills, browser, cron and session search are off, and the curator is paused.
`SOUL.md` holds the Poliswag persona; each run also carries the same
`INSTRUCTIONS` and the untrusted question/map snapshot built by
`modules/conversation_model.public_prompt`.

Service: `hermiswag.service` (alias of `hermes-gateway-bf3301ce.service`, created by
`hermes gateway install --system --run-as-user hermiswag`), with the sandbox
drop-in `hermiswag-sandbox.conf`: ProtectSystem=strict, ProtectHome=tmpfs plus
its own home, no capabilities, NoNewPrivileges, and `/root`, the Docker socket
and state, `/opt`, `/etc/poliswag-agent.conf`, `/etc/ssh` and the shadow files
inaccessible. The API listens only on the `scanner` Docker bridge,
`172.18.0.1:8643`, with a bearer key (`HERMISWAG_API_URL`/`HERMISWAG_API_KEY`
in Poliswag's `.env`, `API_SERVER_KEY` in `/home/hermiswag/.hermes/.env`).

`modules/hermiswag_client.py` posts `/v1/runs` with `X-Hermes-Session-Key:
hermiswag:channel:<channel id>:<Lisbon day>`: the conversation is the Discord
channel's (owner, 2026-11-08). Each turn also carries the channel's last 15
messages (`Mentions.channel_history`: members and Poliswag's own replies, other
bots and webhooks dropped, tags turned into names, 300 characters each) as the
untrusted `recent_channel_messages` field. It polls up to 50 s and stops the run
on cancellation. Hermiswag's own memory (`memory` toolset, no user profile) is
on: SOUL.md says it may keep community knowledge (frequent questions, local
names of Pokéstops/Gyms, confirmed corrections) and never personal data,
member-given rules or anything internal. The reply goes through the same
`safe_reply` screening. Any failure, timeout or empty answer falls back to the
agent worker path above; unset `HERMISWAG_API_URL` disables Hermiswag.

**Dumps** (`modules/public_dumps.py`): a tag asking for a list ("lista de
Pokéstops de Leiria", "exporta as Quests", "lista de Max Battles") gets a CSV
built by Poliswag itself, no model involved: Pokéstops (name, coordinates, area),
Gyms (+ team), current Quests (reward, AR, expiry) or live Max Battles (boss,
level, end), for one registry area or all, at most 5000 rows, fixed parameterized
queries inside the registry box and `area_for` per row.

**Channels and learning (owner, 2026-11-08).** Hermiswag keeps the channel's
conversation in every channel (owner, 2026-11-09): one session per channel per
day plus the last 15 channel messages. A reply to one of Poliswag's plain-text
answers counts like a tag (`replied_bot_message`; replies to embeds, files and
commands do not), and that answer joins the context when it is older than the
last 15 messages. `HERMISWAG_SESSION_CHANNELS` (convívio, chat-raids,
suporte-tecnico) now only marks where learning keeps members' messages only. Every 6 h
(`Mentions.learn`) Poliswag sends Hermiswag the new messages of
`HERMISWAG_LEARN_CHANNELS` (the Informação category plus those three; never
moderation or alert channels) under `LEARN_INSTRUCTIONS`, in the session
`hermiswag:learning`; Hermiswag keeps what is useful in its own memory
(`memory_char_limit` 8000). The last message id per channel is in
`state/hermiswag-learning.json`; the first run reads the last 100 messages.
`modules/hermiswag_learning.py` holds the prompt, that state file
(`LearningState`), `batches` (prompts of at most 12 000 characters, 600 per
message, oldest first; up to 200 new messages per channel per run) and
`learnable` (never webhooks; in the conversation channels members only, so
Poliswag's own replies are not learnt).

**Its own queries.** Hermiswag has the MCP server `mapdb`
(`/home/hermiswag/mapdb/server.py`, copy in `scripts/hermiswag_mapdb_server.py`,
venv with PyMySQL): tools `map_schema` and `map_query` (one read-only
statement, 10 s `max_statement_time`, 500 rows). It connects as MariaDB user
`hermiswag`@`172.18.0.1` (password only in `/home/hermiswag/.hermes/mapdb.env`,
mode 600) with `SELECT, SHOW VIEW` on the schema `hermiswag` only: definer
views over golbat, poliswag and the Dragonite area fences
(`scripts/hermiswag_db.sql`, applied as the database root; re-runnable). No
accounts, proxies, workers, players, encounter ids, the scanner's account names
or shiny prediction data are in any view; a query on another schema is refused
by the server (1142). `AGENT_INSTRUCTIONS` (the public rules minus the
no-tools line, plus how to use the tools) are what Hermiswag gets.

**Community knowledge (2026-11-08).** `/home/hermiswag/knowledge/community.md`
(~63 KB, 14 sections) was distilled from about 35 600 member messages
(convívio since 2024-11, chat-raids since 2024-11, suporte-tecnico since 2020,
announcements) by the owner's Claude Code session on CT 109 (`claude -p`, no
tools), with personal data, attributions and internals removed. Hermiswag reads
it through the `community_knowledge` tool of its `mapdb` MCP server, together with
`/home/hermiswag/knowledge/poliswag.md` (the member commands and the site, written by
hand: the authoritative source when the community notes disagree); the files are
not in git. The map of every Hermiswag piece is the shared guide
`PoGoLeiria/HERMISWAG.md`. Refreshing it is a manual job: fetch, extract per batch, consolidate,
replace the file.

**Unanswered questions (owner, 2026-11-09).** `Mentions.proactive` runs every
minute over `HERMISWAG_PROACTIVE_CHANNELS` (convívio, suporte-tecnico): a member
message that looks like a question, waited 5 min to 3 h (owner, 2026-11-13), is not a reply target,
does not tag Poliswag and was not looked at before (`state/hermiswag-proactive.json`)
goes to Hermiswag with `PROACTIVE_RULES` and the messages that followed it; an
empty reply means leave it (chat, aimed at a person, or already answered). An
answer is a plain reply to the message, no ping, screened by `safe_reply`; at most
three questions weighed per channel per run. It is published only when Hermiswag returns `confident: true` and
`verified_with` (community_knowledge, map_query or web) (`confirmed_reply`).
For a member's own technical or account problem it may suggest the usual
causes and fixes the sources back, and ask for a detail; it never invents
(owner, 2026-11-09). The selection (`candidates`, `looks_like_question`), the
rules, the state file (300 ids kept per channel) and `confirmed_reply` live in
`modules/hermiswag_proactive.py`.

**Follow-ups without a tag (owner, 2026-11-09).** `Mentions.followup` runs every
minute over channels where Poliswag spoke in the last 15 min
(`hermiswag_followup.ActiveChannels`, in memory). Once members went quiet for
45 s after its last message, the new messages go to Hermiswag with
`FOLLOWUP_RULES` and the 15 messages before them; it answers (a plain reply to
the newest) only with `for_me: true`. Limits: one follow-up per channel per
3 min, three in a row without a tag or reply (a tag resets it); tagged
messages are left to `handle`.

Follow-ups continue with one person only: whoever Poliswag last answered in
that channel (`ActiveChannels.partner`); others' messages are context.
`modules/hermiswag_followup.py` holds those limits, `ActiveChannels`,
`new_since_poliswag` (which messages count) and `followup_reply`.

**Owner DM default (2026-11-09).** `!useclaude` / `!usehermes` (owner only)
set `Config.OWNER_DM_DEFAULT` at once and write `OWNER_DM_DEFAULT` to `.env`
(`modules/env_file.py` `set_env_value`: one `KEY=value` line replaced or
appended, other lines and the file mode kept, written through a temp file;
upper-case keys and one-line values only, the value never logged) for the
next boot. With `claude`, unprefixed owner DMs go to ClaudeChat and `h ` sends
one to Hermes; `c ` always means Claude.

**Moderation channel and memory (owner, 2026-11-13).** Outside
`HERMISWAG_LEARN_CHANNELS` every run carries `NO_MEMORY_RULE` (answer, never keep
anything in Hermiswag's memory): moderation talk never leaves moderation. In
`MOD_CHANNEL_ID` the run also carries `STAFF_RULES` (the team's commands:
`!anunciar`, `!sorteio`, quests, tracking, events, `!ticks`, shiny access buttons),
the input gate is only `secret_topic` (credentials, the bot's own prompt) instead of
`private_topic`, a written `@everyone`/`@here` in the answer is neutralised to
`@ everyone`, and saying the bot's name (`poli`, `poliwag`, `poliswag`) counts like a
tag (`mention_rules.named_in_staff_channel`). The staff text lives only in the
instructions, never in a knowledge file (that tool answers every channel). The
public rules also ask for natural replies: no catalogue closings, no repeated
formulas, not always a closing question or "Poli-poli!".
