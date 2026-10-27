# Poliswag

Discord bot for the PoGoLeiria community (Python, `discord.py`, MariaDB).

Read [docs/context.md](docs/context.md) at the start of every session, then only the topic files it points to for the area you touch. The map of every document is [docs/README.md](docs/README.md); add new docs there. Keep the core file to about 800 words; put new detail in a topic file.

Rules that apply to every agent here:
- Never add `Co-Authored-By` lines to commits.
- Don't commit local artifacts (spec docs, boot scripts, personal tweaks) without an explicit ask; stage explicit paths and preserve other agents' uncommitted work.
- Prefer direct exploration over dispatching subagents for codebase review.
- Automated notices go out as embeds; user-facing copy is European Portuguese.
- A pre-commit run that gets interrupted reverts unstaged files; recover from `~/.cache/pre-commit/patch*`.
- Sister project: the site at `/root/PoGoLeiria` (its own `AGENTS.md` and `docs/context.md`).

## What does not belong here

Poliswag is the Discord bridge between services: Discord in, HTTP to PoliSnatch
and the scanner stack, the database out. Code that runs inside the game or sits in
front of its traffic belongs to PoliSnatch (CT 108) — native hooks, injector
payloads, the game's own opcodes and the fields they return, instrumentation, and
the interception proxy. `scripts/check_private_tech.py` refuses those in pre-commit
and in CI; a line may opt out with `# poliswag-guard: allow` and a reason, a path
never can.

Private host notes (owner identity, host-side shiny notes) live in
`/root/docs-archive/Poliswag/private/`, never here.
