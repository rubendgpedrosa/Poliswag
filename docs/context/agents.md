# Discord delegation to Claude and Codex

`cogs/agents.py` accepts requests from `MY_ID` only (including DMs); mods and
other members cannot run or inspect owner jobs. `scripts/agent_worker.py` runs on the
host, using the existing Claude Code / Codex CLI login. `openai` aliases `codex`.

## Usage

- `!self`: capabilities, loaded command count, executor availability, live scanner health.
- `!ask claude <question>` or `!ask openai <question>`: read-only question on any topic.
- `!code claude <task>` or `!code openai <task>`: code edits and local tests.
- `!jobs`: latest ten tasks; `!job <id>`: state and full report; `!cancel <id>`: cancel.
- `!answer <id> <response>`: supply an essential decision to a blocked task.
- Owner requests need a tag of Poliswag, in a DM or a server. Untagged owner
  messages (plain text, `claude <question>`, `code claude <task>`) get no reply
  and start no work; `!` commands are unaffected. Tagged requests accept
  free-form text (Claude by default) and the explicit forms, for example
  `@Poliswag fix the quest bug` or `@Poliswag use OpenAI to fix the quest bug`.
  These go to a code-capable
  general-purpose agent, which can answer questions on any topic or make
  explicitly requested changes. General questions do not require repository
  inspection, and answering a question does not authorize code changes.
  Tasks acknowledge the message immediately with a 👀 reaction and then work until completion,
  making routine decisions without asking permission to continue. Only a genuinely blocking decision
  interrupts the task with one short question. A tagged owner message
  resumes the sole waiting task from that same channel or DM; use `!answer`
  if more than one is waiting there. Requests from other conversations create
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

`cogs/mentions.py` intercepts actual bot mentions anywhere in a message before
command invocation. A bare mention greets rather than listing commands.
Other server members get playful replies, the public event calendar, or
aggregate Pokémon/raid counts for yesterday in Leiria/Marinha Grande.
Conversational DMs from nonowners are silent. Explicit `!help` remains available.

The public path has no repository reader, command invocation, scanner snapshot,
attachments, channel history, or shared owner conversation.
`modules/public_conversation.py` renders a fixed allowlist:
event name/start/end, or bounded numeric totals from pokemon_stats/raid_stats.
Queries and area names are fixed; user text never enters SQL or a reply.

Public banter uses the host's **Codex CLI, GPT-6 Luna, medium reasoning**,
with the existing CLI login. There is no direct Responses API call and no
separate API credential in the bot. `modules/conversation_model.py` submits
only a fixed topic (greeting/events/statistics), never member text or database
rows, to the separate `logs/agent-jobs/public/` queue. Public requests use the
reserved identity `0`; they never appear in owner job listings or DM delivery.
The worker validates the provider, mode and topic again before execution.

Each public run starts in a fresh temporary directory, uses a replacement
mascot instruction file, ignores user configuration and project instructions,
disables shell execution, image reading, web search and multi-agent tools,
and uses a read-only sandbox and ephemeral session. Owner worktrees and
runtime snapshots are never passed to it. Generated text must pass length,
sensitive-topic, identifier, link, mention and code checks. Calendar facts and
statistics are rendered locally and cannot be rewritten by the model.
Worker errors and unsafe output fall back to fixed local replies. Maximum
two waiting public calls per bot, 30-second execution timeout, 35-second queue
wait, bounded output and 60-second error backoff. Queued requests expire after
35 seconds; timeout/cancellation marks them cancelled. A busy worker gives an
immediate local fallback. No raw CLI output or error details are retained for
public runs; request/result queue artifacts follow the existing manual retention
policy. Restart recovery interrupts unfinished public jobs without replay.

No accounts, device/worker details, shiny tables, analytics, code, configuration,
environment, credentials or implementation information enters public replies.
Sensitive-topic requests get a fixed playful deflection, not an explanation.

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
Generated files are declared in the response's `attachments` array as relative
worktree paths. Before publishing completion, the worker copies them into the
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

Every owner job starts from **committed HEAD**, in a new branch
`poliswag-agent/<id>` and worktree `/root/worktrees/poliswag-agent/<id>`.
Uncommitted live-tree changes and production `.env` files are not copied. Tasks
receive a timestamped snapshot of bot identity, loaded commands, executor status,
and scanner health counts (no account names/device identifiers). They read the
repository's `docs/context.md` for architecture.

Worktrees isolate code changes, not processes. The supplied systemd unit makes
live source read-only, hides the production `.env`, other projects, backups, SSH
keys and Docker socket, and allows writes to worktrees, Git metadata, the private
queue, and agent authentication/cache folders. Provider credentials remain
available to the CLIs. Coding tasks can run arbitrary local test commands inside
these filesystem restrictions; tests need available dependencies. Codex also
uses `workspace-write` (or `read-only` for questions); Claude uses an explicit
tool set, only the owner database MCP server, no settings sources, and `dontAsk`
permissions.
No runner command deploys, merges, pushes, commits, or restarts the bot. Agent
instructions forbid those actions as well. Review and apply worktree changes
separately. Shared Git metadata is writable for worktree registration; this
review workflow is not a complete security sandbox.

Owner jobs receive the `poliswag_db` stdio MCP server (`scripts/agent_db_mcp.py`).
Its `list_databases` and `query` tools send private per-job file requests to
`cogs/agents.py`, which executes them through the bot's production connection.
Only running jobs belonging to `MY_ID` are served; public jobs receive no MCP
server. Aliases cover Poliswag, scanner, Dragonite, PoGoLeiria and Poracle.
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
