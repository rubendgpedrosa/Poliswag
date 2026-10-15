# Poliswag

Discord bot for the PoGoLeiria community (Python, `discord.py`, MariaDB).

Read [docs/context.md](docs/context.md) at the start of every session, then only the topic files in `docs/context/` it lists for the area you touch. Keep that core file to about 800 words; put new detail in a topic file.

Rules that apply to every agent here:
- Never add `Co-Authored-By` lines to commits.
- Don't commit local artifacts (spec docs, boot scripts, personal tweaks) without an explicit ask; stage explicit paths and preserve other agents' uncommitted work.
- Prefer direct exploration over dispatching subagents for codebase review.
- Automated notices go out as embeds; user-facing copy is European Portuguese.
- A pre-commit run that gets interrupted reverts unstaged files; recover from `~/.cache/pre-commit/patch*`.
- Sister project: the site at `/root/PoGoLeiria` (its own `AGENTS.md` and `docs/context.md`).
