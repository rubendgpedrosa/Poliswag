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
- Owner DMs accept plain questions on any topic, with no mention or prefix
  required (Claude by default; request Codex to switch). They also accept
  `claude <question>`, `codex <question>`, and
  `code claude <task>` / `code openai <task>`. A leading bot mention accepts
  the same forms in a server. Owner mentions also accept free-form requests,
  for example `@Poliswag fix the quest bug` (Claude by default) or
  `@Poliswag use OpenAI to fix the quest bug`. These go to a code-capable
  general-purpose agent, which can answer questions on any topic or make
  explicitly requested changes. General questions do not require repository
  inspection, and answering a question does not authorize code changes.
  Tasks start silently and work until completion, making routine decisions
  without asking permission to continue. Only a genuinely blocking decision
  interrupts the task with one short question. A plain owner DM or mention
  resumes the sole waiting task; use `!answer` if more than one is waiting.
  The same provider, mode and worktree are preserved, along with the original
  request and decision history. Owner results and questions return by DM.

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
or bots, 15-second member cooldown, five replies per guild per 30 seconds,
bounded cooldown memory, five-second data timeout, 42-second total reply limit,
validated calendar titles and numeric values, disabled mentions, and generic
failure replies without exception text. Missing mention handling fails closed
before command dispatch. Neither public prompts nor exception details are
logged by the mention path.

CLI behavior: [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive)
and [configuration reference](https://developers.openai.com/codex/config-reference).

Owner replies are plain short sentences (maximum 280 characters), witty for
casual chat and factual for code. Successful submission sends no queue,
progress or channel acknowledgement. Code reports remain available via
`!job <id>` as attachments. Statistics, calendars and explicitly requested
detailed reports retain their useful rows/text, with a file for long reports.
Failures get a short generic explanation without publishing raw CLI errors.
Mentions are disabled on every reply.

## Execution and boundaries

The queue is `logs/agent-jobs/` (`AGENT_JOBS_DIR` inside the bot), shared through
the existing `/app`/`logs` bind mounts. Each private job directory contains
`request.json`, `result.json`, structured `response.json`, `answer.txt`, raw
CLI stdout/stderr, and optional `continuation.json`, `cancel` and per-revision
notification markers. Responses declare `done` or `needs_input` and separate
the short sentence from full details. Only completion, a blocking question,
or a terminal failure is delivered automatically. Waiting tasks are not
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
tool set, no MCP servers/settings sources, and `dontAsk` permissions.
No runner command deploys, merges, pushes, commits, or restarts the bot. Agent
instructions forbid those actions as well. Review and apply worktree changes
separately. Shared Git metadata is writable for worktree registration; this
review workflow is not a complete security sandbox.

The worker uses an environment allowlist without Discord or database credentials.
Cancellation/timeouts terminate the process group. A restart marks unfinished
jobs `interrupted` without rerunning them. Worktrees and logs remain for review.
Final DM and blocking-question delivery retries across bot restarts; a crash
between sending and marking delivery can produce a duplicate DM.

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
