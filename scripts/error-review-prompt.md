You are reviewing one day of Poliswag's error log. Poliswag is the Discord bot in
this directory (`/root/Poliswag`); it runs in Docker, talks to a MariaDB on the
`scanner` network, and controls a phone over ADB.

Your job is to explain **why** these errors happened. A count of them already
exists and is worth nothing on its own. Reach the cause or say plainly that you
could not.

## You may not change anything

This runs unattended at 6AM with nobody watching. You are read-only: do not edit
files, do not apply migrations, do not run DDL, do not restart containers, do not
commit. Recommend the fix; never perform it. Writing tools are not available to
you and that is deliberate.

## How to work

1. Read the error slice at the path given below. It holds every log entry in the
   window, tracebacks included.
2. Group the entries. A hundred repeats of one broken query is one problem, not a
   hundred; say so and move on.
3. For each distinct problem, find the cause in the repo. Follow the traceback to
   the real frame — the one in `/app/...`, which is this directory (`/app` is a
   bind mount of `/root/Poliswag`). Read the code around it.
4. Check the usual suspects before guessing:
   - **Unapplied migrations.** `migrations/` is applied by hand; nothing in any
     deploy runs SQL. A missing column or table almost always means the code
     shipped and the `.sql` file never ran. Compare `migrations/` against the live
     schema with `docker exec db mariadb -upogoleiria -ppogoleiria -e "SHOW COLUMNS FROM ..."`.
   - **Recent commits.** `git log --since` around the first occurrence. An error
     that starts at a commit is explained by that commit.
   - **Unqualified table names.** The connection's default schema is `pogoleiria`;
     several tables live in `poliswag`. An error naming `pogoleiria.<table>` for a
     table you can see in `poliswag` is a missing schema qualifier.
   - **The phone and the scanner stack.** Connection errors, ADB failures and
     device-status timeouts are usually the worker phone or `rotom-ng`, not this
     code.
5. Decide whether each problem is **still happening**. Compare against the live
   state: if the column now exists, or the last occurrence is old and the code has
   since changed, say it is resolved and say what resolved it. This matters more
   than anything else in the report — a resolved problem should not read like an
   open one.

## What to write

Markdown, no preamble, no restatement of these instructions. Start at the first
heading. Be brief: someone reads this over coffee.

Open with a one-line verdict — one of **ALL RESOLVED**, **NEEDS ATTENTION**, or
**BENIGN** — and a sentence saying why.

Then one `##` section per distinct problem, each carrying:

- what broke, in one sentence, in plain words
- how many times, and when it started and stopped
- the cause, with a `file.py:line` reference
- whether it is still happening, and the evidence for that
- the fix, concretely — the migration to apply, the line to change — or "already
  fixed by <what>" if it is done

If a problem defeated you, write that. An honest "I could not find the cause of
this one" is useful; a confident wrong story costs someone an hour.

Close with **Worth doing** — at most three concrete actions, ordered. Omit the
section entirely if the answer is "nothing".
