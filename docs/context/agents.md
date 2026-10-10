# Discord delegation to Claude and Codex

`cogs/agents.py` accepts requests from `MY_ID` only (including DMs); mods and
other members cannot run or inspect jobs. `scripts/agent_worker.py` runs on the
host, using the existing Claude Code / Codex CLI login. `openai` aliases `codex`.

## Usage

- `!self`: capabilities, loaded command count, executor availability, live scanner health.
- `!ask claude <question>` or `!ask openai <question>`: read-only diagnostic task.
- `!code claude <task>` or `!code openai <task>`: code edits and local tests.
- `!jobs`: latest ten tasks; `!job <id>`: state and result; `!cancel <id>`: cancel.
- Owner DMs also accept `claude <question>`, `codex <question>`, and
  `code claude <task>` / `code openai <task>`. A leading bot mention accepts
  the same forms in a server. Other conversation follows existing bot behavior.

Results arrive by owner DM, with mentions disabled. Long answers attach a text
file. No Discord notification is sent until an owner submits work.

## Execution and boundaries

The queue is `logs/agent-jobs/` (`AGENT_JOBS_DIR` inside the bot), shared through
the existing `/app`/`logs` bind mounts. Each private job directory contains
`request.json`, `result.json`, `answer.txt`, raw CLI stdout/stderr, and optional
`cancel` / `notified` markers. JSON writes use atomic replacement. The worker
heartbeat expires after 30 seconds. Maximum five outstanding requests per owner;
one executes at a time, with a 20-minute limit. Systemd caps resources.

Every job starts from **committed HEAD**, in a new branch
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
Final DM delivery retries across bot restarts; a crash between sending and
marking delivery can produce a duplicate DM.

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
