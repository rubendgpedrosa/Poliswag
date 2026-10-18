# Discord delegation to Claude and Codex

`cogs/agents.py` accepts requests from `MY_ID` only, and only from a message
that tags Poliswag (a DM is not a tag); mods and other members cannot run or
inspect owner jobs. Owner agents are **read-only**: they answer and propose
diffs but never edit code. `scripts/agent_worker.py` runs on the
host, using the existing Claude Code / Codex CLI login. `openai` aliases `codex`.

## Usage

- `!self`: capabilities, loaded command count, executor availability, live scanner health.
- `!jobs`: latest ten tasks; `!job <id>`: state and full report; `!cancel <id>`: cancel.
  None of these starts or resumes an agent.
- Every agent request needs a tag of Poliswag, in a DM or a server. There are
  no `!ask`/`!code`/`!answer` commands; untagged owner messages (plain text,
  `codex <question>`, `!code codex <task>`) get no reply and start no work.
  Tagged requests accept free-form text (Claude by default) and the explicit
  forms `codex <question>` / `ask codex <question>`, for example
  `@Poliswag why did the quest export fail?` or `@Poliswag use Codex to explain
  the quest bug`. `@Poliswag answer <id> <response>` answers a named blocked
  task. These go to a read-only general-purpose agent, which answers questions
  on any topic; asked for a code change, it explains it and proposes a diff in
  its report instead of applying it.
  Tasks acknowledge the message immediately with a 👀 reaction and then work until completion,
  making routine decisions without asking permission to continue. Only a genuinely blocking decision
  interrupts the task with one short question. A tagged owner message
  resumes the sole waiting task from that same channel or DM; use
  `@Poliswag answer <id> <response>` if more than one is waiting there. Requests from other conversations create
  separate jobs and keep their own reply destinations. The origin is stored
  separately from explicit delivery overrides. For older jobs, the stored
  destination identifies the conversation (DM when absent).
  The same provider, mode and worktree are preserved, along with the original
  request and decision history. Owner results and questions return to the stored
  destination: the originating channel by default, or DM for a DM request.
  Explicit `send via DM` / `envia por DM` overrides the origin; `send to
  #moderação`, `envia para <#channel-id>` or `send to channel name` chooses a
  channel. Channel names must match exactly (case/accents ignored) and be
  unambiguous. Unknown channels fail submission instead of redirecting the
  result. Continuations retain the original destination. Legacy jobs without
  destination metadata keep their original DM behavior; `!job` stays private.

## Public mentions

`cogs/mentions.py` intercepts a visible, unescaped bot tag in message content
before command invocation. Code blocks, inline code, blockquotes, escaped tags,
other users' tags and reply references/auto-pings do not count; untagged chat
(even containing the literal name Poliswag) triggers no model or data lookup.
A bare mention greets rather than listing commands. Other server members get
useful, concise replies to their actual question, the public event calendar, or
aggregate Pokémon/raid counts for yesterday in Leiria/Marinha Grande.
Conversational DMs from nonowners are silent. Explicit `!commands` remain
available and are not intercepted. Replies keep Portuguese European prose but
use English game terms (Egg, Lure, Pokéstop, Gym, Raid, Quest, Shiny, Lucky,
100IV, Friend Code, Team GO Rocket, species/moves/items/weather/types); local
fallback labels use Gym and Pokéstop.

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
capability/authorization claims while keeping normal game terms, trainer/Gym
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
