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
exception details or a fallback to another agent. Each message receives an immediate 👀 (progress feedback is described above); the response returns to that origin with mentions disabled,
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
indicator on and, if the turn lasts over three seconds, replies with one live
status embed edited every four seconds: elapsed time, the last five tool calls
(✓/✗ with duration), the current tool with a short preview, and Hermes' latest
commentary. Previews and commentary pass the same credential filter as approval
cards. On completion the embed collapses to a summary (tool count, time, errors)
and the origin message gets ✅, ⚠️ (partial) or ❌ after 👀.

The embed carries owner-only buttons (same identity/DM checks as approvals):
**Parar** calls `POST /v1/runs/<id>/stop`; **Redirecionar** opens a modal whose
text goes to `POST /v1/runs/<id>/steer` for the running agent. A turn queued
behind another shows **Na fila** with **Enviar à tarefa atual**: the queued
request leaves the local queue (only while still queued, never after admission)
and is sent as steer guidance to the active run instead (💬 reaction); if the run
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

## Explicit Claude/Codex jobs

`cogs/agents.py` accepts tagged requests from `MY_ID` in DMs only. Owner jobs remain
**read-only**: they answer and propose diffs but never edit code.
`scripts/agent_worker.py` runs on CT 105 using the existing Claude Code / Codex
CLI login. `openai` aliases `codex`. These are separate jobs, not turns in the
Hermes conversation.

- `!self`: capabilities, command count, executor availability and scanner health.
- `!jobs`: latest ten tasks; `!job <id>`: state/full report; `!cancel <id>`: cancel.
- `@Poliswag codex <question>`, `@Poliswag ask claude <question>` and legacy
  `code <provider> <question>` select the old worker explicitly. Natural forms
  beginning `use Codex …` / `usa Claude …` also work. Merely mentioning a
  provider (such as “How does Codex work?”) stays in Hermes.
- `@Poliswag answer <id> <response>` resumes a named blocked task. A tagged
  owner DM resumes the sole waiting task in that origin before starting
  default Hermes chat; multiple waiting jobs prompt for the task ID. Explicit
  provider requests retain their existing parser priority. Plain owner DMs
  enter Hermes directly and do not resume legacy jobs.

Jobs retain provider, worktree, original request and decisions. Origin and
explicit delivery overrides remain separate. Results return to the stored
origin unless explicitly directed via `send via DM` / `envia por DM`, or an
unambiguous `send to #channel` / `envia para <#id>` reference. Continuations
retain their original destination; `!job` stays private. Missing legacy
destination metadata keeps DM delivery. These commands never start or resume
jobs without a tag.

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

Public replies use the host's **Codex CLI, GPT-6 Luna, low reasoning**, with the
existing CLI login. There is no direct Responses API call and no separate API
credential in the bot. `modules/conversation_model.py` submits the sanitized
question plus a strictly validated public map snapshot to the separate
`logs/agent-jobs/public/` queue. The queue prompt field still carries a fixed
topic (greeting/events/statistics/map); the question and snapshot travel in a
validated context object. Public requests use reserved identity `0`; they never
appear in owner job listings or DM delivery. The worker validates owner, provider,
mode, topic, question text, snapshot schema/types/sizes again before execution.

`modules/public_map_context.py` supplies the only map data. It receives the
scanner query interface and a copy of public species names (never the service,
bot or Config). Queries are fixed, read-only, parameterized and limited to a
fixed Leiria/Marinha Grande bounding box (lat 39.6–39.9, lon −9.1–−8.65):
live encounter spawns (`seen_type='encounter'`, verified expiry, future
`expire_timestamp`), current gym Raids (`deleted=0`, `raid_level>0`, future
`raid_end_timestamp`; Eggs distinguished from active bosses and include a
validated future `hatches_at` when available), live Lures
(`deleted=0`, future `lure_expire_timestamp`), and current normal/alternative
quests (`deleted=0`, appropriate future expiry). Only known public fields are
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
injection cannot add tools, permissions or owner context. Owner worktrees and
runtime snapshots are never passed to it. Generated text may contain numbers,
accents, line breaks, game terms and public `pogoleiria.pt` links (including
`/mapa/@...`), but must pass length (about 1800 characters), credential,
code/backtick, internal-path, mention and foreign-link checks. Calendar facts
and statistics are rendered locally, passed to the model as a strict
`facts` context field, and still appended authoritatively; the model cannot
rewrite them and a no-data claim is replaced by the local facts. Worker errors
and unsafe output fall back to helpful local replies, never a fake success or a
fixed joke. Maximum two
waiting public calls per bot, 30-second execution timeout, 35-second queue
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
bounded cooldown memory, five-second data timeout, 42-second total reply limit,
validated calendar titles and numeric values, disabled mentions, and generic
failure replies without exception text. Missing mention handling fails closed
before command dispatch. Neither public prompts nor exception details are
logged by the mention path.

CLI behavior: [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive)
and [configuration reference](https://developers.openai.com/codex/config-reference).

Owner replies use regular text containing the LLM's natural answer, preserving
sentences and paragraphs without a 280-character or one-sentence limit.
Submission immediately adds a 👀 reaction to the originating message; it does
not post a canned acknowledgement. Public requests use the same reaction.
Replies wait for completion or a blocking decision. Answers exceeding the
2000-character message limit include the full text as an attachment.
Code reports remain available via
`!job <id>` as attachments. Statistics, calendars and explicitly requested
detailed reports retain their useful rows/text, with a file for long reports.
Owner agents are read-only and cannot create files, so they return an empty
`attachments` array and put tables in text (long text is sent as a file); the
staging below stays as a fail-closed path. Generated files are declared in the
response's `attachments` array as relative worktree paths. Before publishing completion, the worker copies them into the
shared job directory under `artifacts/<revision>/`; the container does not need
access to host worktrees. The bot sends these files and any long text report
together. Missing/invalid files fail staging or delivery instead of silently
sending only text. Paths must stay inside the worktree (no hidden paths or
escaping symlinks); supported types are PNG, JPEG, GIF, WebP, PDF, CSV, TXT,
JSON and SVG. Maximum nine generated files, 8 MiB per file and 24 MiB total.
Failures get a short generic explanation without publishing raw CLI errors.
Mentions are disabled on every reply.

## Execution and boundaries

The queue is `logs/agent-jobs/` (`AGENT_JOBS_DIR` inside the bot), shared through
the existing `/app`/`logs` bind mounts. Each private job directory contains
`request.json`, `result.json`, structured `response.json`, `answer.txt`, raw
CLI stdout/stderr, private `database/` MCP requests/results while executing,
staged output `artifacts/`, and optional `continuation.json`, `cancel` and per-revision
notification markers. Responses declare `done` or `needs_input` and separate
the short sentence from full details. Only completion, a blocking question,
or a terminal failure is delivered automatically. Conversational replies and
follow-up questions use regular text; request errors and status commands retain embeds. Waiting tasks are not
rerun by polling or restart recovery; an owner answer atomically queues
the next revision, and each revision is delivered once. JSON writes use atomic replacement. The worker
heartbeat expires after 30 seconds. Maximum five outstanding requests per owner;
one executes at a time, with a 20-minute limit. Systemd caps resources.

Every owner job reads a checkout of **committed HEAD**, in a new branch
`poliswag-agent/<id>` and worktree `/root/worktrees/poliswag-agent/<id>`.
Uncommitted live-tree changes and production `.env` files are not copied. Tasks
receive a timestamped snapshot of bot identity, loaded commands, executor status,
and scanner health counts (no account names/device identifiers). They read the
repository's `docs/context.md` for architecture.

The supplied systemd unit mounts `/root` as an empty tmpfs and binds back only
the live source (read-only, production `.env` hidden), `~/.local`, worktrees and
the agent folders, so other stacks' configs, backups and SSH keys are absent;
`/var/lib/docker`, `/opt`, `/etc/shadow` and the Docker socket are hidden, and allows writes to worktrees, Git metadata, the private
queue, and agent authentication/cache folders. Provider credentials remain
available to the CLIs. Owner jobs are read-only: `AgentJobs.submit` always stores
mode `ask`, the worker refuses any other mode, Codex runs with `--sandbox
read-only`, and Claude gets only Read/Grep/Glob, the owner database MCP server,
no settings sources and `dontAsk` permissions. When the delivery destination is
a channel, the prompt tells the agent members can read it and forbids
credentials, account usernames, device/worker identifiers and internal details.
No runner command deploys, merges, pushes, commits, or restarts the bot. Agent
instructions forbid those actions as well. Shared Git metadata is writable for worktree registration; this
review workflow is not a complete security sandbox.

Owner jobs receive the `poliswag_db` stdio MCP server (`scripts/agent_db_mcp.py`).
Its `list_databases` and `query` tools send private per-job file requests to
`cogs/agents.py`, which executes them through the bot's production connection.
Only running jobs belonging to `MY_ID` are served; public jobs receive no MCP
server. Aliases cover Poliswag, scanner, PoGoLeiria and Poracle. Dragonite
(scanner accounts, passwords, refresh tokens) is not reachable: the bot's DB
user can read every schema, so `modules/agent_database.py` refuses SQL that
names the `dragonite`/`mysql`/`performance_schema`/`sys` schemas, any
`*account*` identifier, credential/username/Friend Code columns, SQL
comments, set operations or CTE column lists; `SHOW` and `information_schema`
are limited to schema discovery (no processlist, binlog, InnoDB or
query-cache text, which can carry other sessions' token updates). It redacts
those columns by name (including `table.column`) when `SELECT *` returns them. Table and
schema blocks are by name and hold; column redaction is best effort. A
dedicated read-only DB user with grants would be the hard boundary.
Queries run in a read-only transaction with a 10-second statement limit,
maximum 500 rows and bounded output. Schema discovery and aggregate queries
use live data; credentials stay inside the bot. SQL writes and file queries
are rejected. Cancellation and bridge waits are bounded.

`spawn_history_report` computes the unfiltered shiny leaderboard over 1–48
hours from the independent raw archive. It returns per-trainer aggregates and
coverage metadata, with NULL hourly rates for an incomplete window. Owner
workers are instructed to use it instead of filtered alert counts. It shares
the private bridge's owner/running-job gate and reads in a consistent read-only
transaction. See [spawn history](spawn-history.md) for the recorded odds and
retention semantics.

The worker uses an environment allowlist without Discord or database credentials.
Cancellation/timeouts terminate the process group. A restart marks unfinished
jobs `interrupted` without rerunning them. Worktrees and logs remain for review.
Final result and blocking-question delivery retries across bot restarts in the
stored destination, with no fallback to a different channel or DM. The delivery
marker is written only after the complete Discord send succeeds. One failed
delivery does not block other finished jobs. A crash between sending and marking
delivery can still produce a duplicate message.

## Host installation

Both `claude` and `codex` must be installed and logged in on the host. Check with
`claude auth status` and `codex login status`. The bot container needs no new
runtime packages or mounts (Git is installed in the image for worker tests).
Install `bubblewrap` on the host for Codex's Linux sandbox. The service retains
only SETUID/SETGID/SETFCAP for the sandbox's user namespace mapping; mount and
ptrace capabilities are excluded and privilege escalation is disabled.

```sh
apt-get install -y bubblewrap
mkdir -p /root/Poliswag/logs/agent-jobs /root/worktrees/poliswag-agent /root/.cache
chmod 700 /root/Poliswag/logs/agent-jobs /root/worktrees/poliswag-agent
# Use the same numeric owner ID as MY_ID, without a Discord token:
printf 'POLISWAG_AGENT_OWNER_ID=YOUR_DISCORD_ID\n' > /etc/poliswag-agent.conf
chmod 600 /etc/poliswag-agent.conf
cp scripts/agent-worker.service /etc/systemd/system/poliswag-agent.service
systemctl daemon-reload
systemctl enable --now poliswag-agent.service
docker restart poliswag
```

Check `systemctl status poliswag-agent` / `journalctl -u poliswag-agent`, then
`!self`. Disable with `systemctl disable --now poliswag-agent`. Artifacts have
no automatic retention deletion: remove reviewed worktrees with
`git worktree remove <path>` and delete the corresponding job directory.
