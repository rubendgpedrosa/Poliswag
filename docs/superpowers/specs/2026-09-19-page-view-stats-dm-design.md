# Page-view stats DM — design

**Date:** 2026-09-19
**Status:** Approved for planning
**Reads:** `pogoleiria.page_view`, written by the landing app ([page-view tracking](../../../../PoGoLeiria/docs/superpowers/specs/2026-09-15-page-view-tracking-design.md), [device detail](../../../../PoGoLeiria/docs/superpowers/specs/2026-09-19-device-detail-tracking-design.md)).

## Goal

One admin command that answers "who is using the site and how" without opening a SQL client: Poliswag queries `pogoleiria.page_view` on demand and DMs a formatted report to `MY_ID`.

- On demand only. No `@tasks.loop`, no cron, no hook into `cogs/scheduled.py`.
- Read-only. Poliswag never writes to the `pogoleiria` schema.
- The report is the DM. The invoking channel never shows statistics.

## Scope

| In | Out (later, if wanted) |
|---|---|
| `!webstats [período]` prefix command, admin-gated | Slash command — the bot has zero `app_commands`; `!` is the whole surface |
| DM to `MY_ID`, always, regardless of who invoked | Scheduled/daily digest — `scheduled.py` stays untouched |
| Read-only `DatabaseConnector(Config.DB_POGOLEIRIA)` | Any write, `ALTER`, or migration against `pogoleiria` |
| Periods `1d` / `7d` / `30d` / `Nd` / `all`, default `7d` | Comparison vs previous period, trend arrows |
| One embed: inline KPI fields + fenced-table fields, `.txt` attachment on overflow | A rendered HTML/PNG report via `imgkit` — considered and rejected, see [Rejected](#rejected-a-rendered-html-image) |
| Graceful handling of the seven optional device columns being absent or all-`NULL` | Cross-day visitor retention — impossible, see [Visitor caveat](#visitor-caveat) |
| `pogoleiria.page_view` seeded into `mock_database/init.sql` | A `/stats` web page (the landing repo's own out-of-scope item) |

## Command surface

| Decision | Value | Why |
|---|---|---|
| Name | `!webstats` | `!stats` is free but one keystroke from the existing `!status`; `!webstats` cannot be mistyped into it |
| Aliases | none | `!help` already lists it; a second name only widens the collision surface |
| Type | `@commands.command` (prefix) | Every one of the 19 existing commands is prefix-based; `tree.sync()` currently syncs an empty tree |
| Who may run it | `MY_ID` only | Invoker and recipient are the same person; no one can request a report they cannot read |
| Argument | `período`, optional, default `7d` | |
| Cog | `cogs/webstats.py`, class `WebStats` | |
| Help label | `_COG_DISPLAY_NAMES["WebStats"] = "📊 Estatísticas (admin)"` in `modules/help_command.py` | Every cog with commands has an entry |

### Period argument

`resolve_period(arg) -> int | None` — pure, in `modules/page_view_stats.py`.

| Input | Days | Note |
|---|---|---|
| omitted | 7 | default |
| `hoje`, `today`, `1d`, `1` | 1 | UTC day, not local |
| `7d`, `7`, `30d`, `30`, `Nd` | N | `1 ≤ N ≤ 365` |
| `all`, `tudo`, `sempre` | `None` | no lower bound |
| anything else, `0d`, `400d` | — | raises `ValueError` |

`since_for(days)` → `datetime(1970, 1, 1)` when `days is None`, else UTC midnight of `today_utc - (days - 1)`. A period is therefore whole **UTC calendar days including today**, which is what makes the per-day rows line up with the `visitor` hash day.

> **UTC is load-bearing.** `created_at` is written by MariaDB whose `time_zone` is `SYSTEM` = UTC, while the `poliswag` container's local time is WEST (UTC+1). `datetime.now()` would put the boundary an hour into the future and silently drop the first hour of the oldest day. Use `datetime.now(timezone.utc)` and label every time in the report "UTC".

### Authorization

```python
def cog_check(self, ctx):
    return str(ctx.author.id) == str(Config.MY_ID)
```

- **`MY_ID` only** — deliberately narrower than every other cog, which gates on `ADMIN_USERS_IDS`. The same id both invokes and receives: there is no case where someone can run a report they cannot read.
- An admin in `ADMIN_USERS_IDS` who is not `MY_ID` is refused, silently, exactly like a stranger. A test asserts this so the gate cannot be widened by accident.
- Unauthorized invocation is silent — no reply, no reaction:

```python
async def cog_command_error(self, ctx, error):
    if isinstance(error, commands.CheckFailure):
        return
    ...
```

  `tracker.py` / `lures.py` are silent only by omission: their `CheckFailure` bubbles into discord.py's logger as a traceback. Swallowing it explicitly keeps the observable behaviour identical and stops the log noise. The loud variant in `container_manager.py` ("❌ Não tens autorização") is deliberately *not* copied — a stats dump should not tell a stranger it exists.

### Where output goes

| Situation | Channel | DM to `MY_ID` |
|---|---|---|
| Valid invocation in a guild channel | invoking message deleted, nothing sent | full report |
| Valid invocation in a DM with the bot | invoking message kept (cannot delete another user's DM message) | full report |
| Bad `período` | usage embed via `ctx.send` | nothing, no DB query |
| `MY_ID` is `0` / unset | config-error embed via `ctx.send` | nothing, no DB query |
| DB unreachable | error embed via `ctx.send` | nothing |
| `discord.Forbidden` on `user.send` | nothing | — → one-line notice to `MOD_CHANNEL` |

Message deletion mirrors `scheduled.py`'s `if not isinstance(ctx.channel, discord.DMChannel): await ctx.message.delete()`, and happens **after** argument validation so a typo still gets a visible hint. Validation errors are the only thing the channel ever renders; statistics are not, which is why a DM failure falls back to a *notice*, never to the payload.

Recipient resolution:

```python
user = self.poliswag.get_user(Config.MY_ID) or await self.poliswag.fetch_user(Config.MY_ID)
```

`get_user` is a cache hit in the common case; `fetch_user` covers a cold cache. `Config.MY_ID` is already an `int` (`config.py`), so no cast.

## Data layer

`modules/page_view_stats.py` — `class PageViewStats(LoggingMixin)`, instantiated in `Poliswag.__init__` as `self.page_view_stats` and reached from the cog as `self.poliswag.page_view_stats`, the same shape as every other service.

| Concern | Decision |
|---|---|
| Connection | `DatabaseConnector(Config.DB_POGOLEIRIA)` — the existing `DB_USER=pogoleiria` already holds `GRANT ALL ON pogoleiria.*`, so no new credentials |
| Creation | **Lazy**, on first command use, cached on the instance |
| New DB layer | None. Reuses `modules/database_connector.py` exactly as `lure_manager.py` and `quest_search.py` do |

> **Why lazy matters.** `DatabaseConnector.__init__` connects eagerly and re-raises `pymysql.MySQLError`. Services are constructed in `Poliswag.__init__`, so an eager connector would make an unreachable or absent `pogoleiria` schema crash the whole bot at boot — and `mock_database/init.sql` creates `scanner`, `poliswag`, `poracle` and `dragonite` but **not** `pogoleiria`, so that crash is the guaranteed outcome in dev today. A lazy connector degrades to one failed command instead.

> **No `%` in any SQL.** `DatabaseConnector._execute_query_sync` calls `cursor.execute(query, params)`, and pymysql applies `query % args` whenever `params` is not `None`. A `DATE_FORMAT(created_at, '%Y-%m-%d')` would raise `ValueError: unsupported format character`. Every statement below therefore uses `DATE()` / `HOUR()` and formats dates in Python; the only `%` in the file is `%s`.

`collect(since) -> dict` runs the statements below, each as `get_data_from_database(SQL, params=(since,))`, and returns the raw rows. No formatting, no Discord types.

### Optional-column detection

```sql
SELECT COLUMN_NAME FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'page_view'
  AND COLUMN_NAME IN %s;
```

- Run once per invocation, before the optional statements. Result is a `set[str]`. `IN %s` takes the tuple below directly — pymysql expands a Python tuple into a parenthesised list — so the constant stays the single source of truth.
- The set is final at **seven** columns, per the sibling device-detail spec:

```python
_OPTIONAL_COLUMNS = (
    "os_version", "browser_version", "model",
    "arch", "bitness", "cpu_cores", "device_memory",
)
```

- Detection is data-driven precisely so this tuple is the only place the list exists: no statement, section or test enumerates the columns anywhere else.
- Today the query returns **zero rows** — the `ALTER` has not been applied — and the optional fields are simply absent.
- A column present but all-`NULL` needs no special case: its statement carries `WHERE col IS NOT NULL` and returns no rows, and an empty section is dropped.

## Statistics

All statements are against `page_view` with `WHERE created_at >= %s` (`since`). Per-period reach is counted as **sessions** (`COUNT(DISTINCT load_id)`) or **views** (`COUNT(*)`) — never `COUNT(DISTINCT visitor)`, see [Visitor caveat](#visitor-caveat).

### 1. Period totals (inline KPI fields)

```sql
SELECT COUNT(*) views, SUM(is_load) loads, COUNT(DISTINCT load_id) sessions,
       COUNT(DISTINCT CASE WHEN standalone = 1 THEN load_id END) pwa_sessions,
       COUNT(DISTINCT DATE(created_at)) days
FROM page_view WHERE created_at >= %s;
```

Derived in Python: `views / sessions`, `pwa_sessions / sessions`, both guarded against `sessions = 0`. These five scalars plus the daily-uniques mean/max from §2 are the embed's six `inline=True` fields; `days` is the denominator for that mean and is printed next to the period.

### 2. Daily traffic

```sql
SELECT DATE(created_at) d, SUM(is_load) loads, COUNT(*) views,
       COUNT(DISTINCT load_id) sessions, COUNT(DISTINCT visitor) visitors
FROM page_view WHERE created_at >= %s
GROUP BY d ORDER BY d;
```

- `visitors` is valid **per row** (one UTC day = one hash generation) and is the only place a visitor count appears.
- Rendered as days when the period spans ≤ 14 days; above that, Python rolls the same rows up to ISO weeks (`loads`/`views`/`sessions` summed, `visitors` shown as the mean of that week's days). The rollup is a pure function over the returned rows — no second query.
- `média / máx de únicos` is averaged over **days that have rows**, and the report prints that denominator, so a period longer than the site's lifetime is not diluted by empty days.

### 3. Hour-of-day activity

```sql
SELECT HOUR(created_at) h, COUNT(*) views
FROM page_view WHERE created_at >= %s GROUP BY h ORDER BY h;
```

Rendered as a fixed 24-character sparkline (`·` for zero, `▁▂▃▄▅▆▇█` scaled to the busiest hour) plus the peak hour in text. One line, no table.

### 4. View / window split

```sql
SELECT `view`, SUM(is_load) loads, SUM(1 - is_load) switches, COUNT(*) views
FROM page_view WHERE created_at >= %s
GROUP BY `view` ORDER BY views DESC;
```

`view` is a reserved-ish word in MariaDB contexts — always backticked. `loads` = document loads (entry points), `switches` = in-app navigations. No session column: one session spans several views, so `COUNT(DISTINCT load_id)` per view would sum above the period's session count.

### 5. Device / OS / browser

```sql
SELECT device, COALESCE(os, 'n/d') os, COALESCE(browser, 'n/d') browser,
       COUNT(DISTINCT load_id) sessions, COUNT(*) views
FROM page_view WHERE created_at >= %s
GROUP BY device, os, browser ORDER BY sessions DESC, device LIMIT 10;
```

One combined row per real-world configuration. At current cardinality (7 combinations over all 109 rows) the cube is smaller and more informative than three separate lists.

### 6. Screen width

```sql
SELECT CASE WHEN screen_w IS NULL THEN 'n/d'
            WHEN screen_w < 380  THEN '<380'
            WHEN screen_w < 430  THEN '380-429'
            WHEN screen_w < 768  THEN '430-767'
            WHEN screen_w < 1280 THEN '768-1279'
            ELSE '>=1280' END bucket,
       MIN(screen_w) mn, COUNT(DISTINCT load_id) sessions
FROM page_view WHERE created_at >= %s
GROUP BY bucket ORDER BY mn;
```

`ORDER BY mn` because the labels do not sort lexicographically. Buckets are CSS px and chosen around the breakpoints that matter: small phone, standard phone, large phone/small tablet, tablet, desktop. Six buckets by construction, so no `LIMIT` is needed.

### 7. Language

```sql
SELECT COALESCE(lang, 'n/d') lang, COUNT(DISTINCT load_id) sessions
FROM page_view WHERE created_at >= %s GROUP BY lang ORDER BY sessions DESC LIMIT 6;
```

### 8. Country

```sql
SELECT COALESCE(country, 'n/d') country, COUNT(DISTINCT load_id) sessions
FROM page_view WHERE created_at >= %s GROUP BY country ORDER BY sessions DESC LIMIT 5;
```

Currently 100 % `PT`. Kept because a non-`PT` row is the interesting signal, and one line costs nothing.

### 9. Referrers

```sql
SELECT COALESCE(referrer, '(direto)') referrer, COUNT(DISTINCT load_id) sessions
FROM page_view WHERE created_at >= %s GROUP BY referrer ORDER BY sessions DESC LIMIT 6;
```

`referrer` is a bare hostname on load rows only (`NULL` on switches and on same-host navigation), so `(direto)` legitimately dominates.

### 10. Paths

```sql
SELECT path, COUNT(*) views, COUNT(DISTINCT load_id) sessions
FROM page_view WHERE created_at >= %s GROUP BY path ORDER BY views DESC LIMIT 6;
```

Separates `/mapa` from deep links like `/mapa/pokemon/<id>` — the only way to see shared-link traffic. Paths are ellipsised to 38 chars in the render.

### 11–14. Optional device columns

Seven columns, four embed fields. Each statement runs only when detection found its column, and returns nothing while every row is `NULL`. The four low-cardinality hardware columns share one field — separately they would be four fields of two or three rows each, and the 25-field budget is better spent elsewhere.

```sql
-- 11. os_version
SELECT COALESCE(os, 'n/d') os, os_version, COUNT(DISTINCT load_id) sessions
FROM page_view WHERE created_at >= %s AND os_version IS NOT NULL
GROUP BY os, os_version ORDER BY os, os_version + 0 DESC LIMIT 10;

-- 12. browser_version
SELECT COALESCE(browser, 'n/d') browser, browser_version, COUNT(DISTINCT load_id) sessions
FROM page_view WHERE created_at >= %s AND browser_version IS NOT NULL
GROUP BY browser, browser_version ORDER BY browser, browser_version + 0 DESC LIMIT 10;

-- 13. model
SELECT model, COUNT(DISTINCT load_id) sessions
FROM page_view WHERE created_at >= %s AND model IS NOT NULL
GROUP BY model ORDER BY sessions DESC LIMIT 8;

-- 14a/14b. arch, bitness — identical shape, one statement each
SELECT arch val, COUNT(DISTINCT load_id) sessions
FROM page_view WHERE created_at >= %s AND arch IS NOT NULL
GROUP BY val ORDER BY sessions DESC LIMIT 6;

-- 14c/14d. cpu_cores, device_memory — numeric, ordered by value
SELECT cpu_cores val, COUNT(DISTINCT load_id) sessions
FROM page_view WHERE created_at >= %s AND cpu_cores IS NOT NULL
GROUP BY val ORDER BY val LIMIT 8;
```

| Column | Type (sibling spec) | Ordering | Cap | Field |
|---|---|---|---|---|
| `os_version` | `VARCHAR(12)` | `os`, then `os_version + 0` desc | 10 | Versões de OS |
| `browser_version` | `VARCHAR(12)` | `browser`, then `browser_version + 0` desc | 10 | Versões de browser |
| `model` | `VARCHAR(40)` | sessions desc | 8 | Modelos |
| `arch` | `VARCHAR(12)` | sessions desc | 6 | Hardware |
| `bitness` | `VARCHAR(2)` | sessions desc | 6 | Hardware |
| `cpu_cores` | `TINYINT UNSIGNED` | value asc | 8 | Hardware |
| `device_memory` | `DECIMAL(4,2)` | value asc | 6 | Hardware |

`ORDER BY col + 0` sorts `"9"` below `"10"`; the two version columns are `VARCHAR`. `device_memory` prints as returned from `DECIMAL(4,2)` — `0.25`, `8.00` — because the browser's quantized ladder is the real resolution and rounding to an integer would merge `0.25` with `0.5`.

Each optional field carries a coverage line — `cobertura 41/67 sessões (61 %)` — computed in Python from that section's session sum against the period total. A Safari or Firefox visitor legitimately has no client hint, and `arch` / `bitness` / `device_memory` are Chromium-desktop-only in practice, so a low count must read as "not reported", never as "nobody".

## Visitor caveat

`visitor` is `HMAC(secret, ip | ua | YYYY-MM-DD UTC)` truncated to 16 hex. It **rotates at UTC midnight**, so the same person is a different value tomorrow.

| Allowed | Forbidden |
|---|---|
| `COUNT(DISTINCT visitor)` grouped by `DATE(created_at)` | `COUNT(DISTINCT visitor)` over any multi-day window |
| "média diária de únicos", "máx diário de únicos" | "visitantes únicos deste mês" |
| `COUNT(DISTINCT load_id)` for period reach | Any retention, returning-visitor or cohort metric |

The report states this in the embed footer: `visitor roda à meia-noite UTC — únicos só por dia; sessões para o período.` Every non-daily section counts sessions, so the forbidden shape is structurally absent rather than merely discouraged.

> The sibling device-detail spec's "Useful queries" use `COUNT(DISTINCT visitor)` over `INTERVAL 30 DAY`. Those over-count by roughly the number of days a person visits. They are fine as ad-hoc SQL for a rough ordering; they are not copied into this report.

## Message format and Discord limits

`modules/page_view_report.py` — pure functions. No DB, no `discord` import beyond building the `Embed`.

```
collect(since) -> dict                            # page_view_stats, the only I/O
build_sections(stats, period) -> Report           # Report = (kpis, sections)
                                                  #   kpis     = list[(label, value)]
                                                  #   sections = list[(title, rows, note)]
render_table(section) -> str                      # fixed-width, width-budgeted
build_dm_embed(report) -> discord.Embed | None    # None when it would not fit
render_text_report(report) -> str                 # same content, no caps, for the .txt
```

### Inline KPI fields vs fenced tables

Discord's own layout primitive is `inline=True`: it packs fields into rows of up to three and reflows correctly on mobile. Scalars use it; tables do not.

| Field | `inline` | Value |
|---|---|---|
| Loads | `True` | `67` |
| Views | `True` | `109` |
| Sessões | `True` | `67` |
| Views/sessão | `True` | `1.63` |
| Únicos/dia | `True` | `9.4 média · 16 máx` |
| PWA instalada | `True` | `2 de 67 (3 %)` |
| Tráfego diário | `False` | fenced table |
| Atividade por hora (UTC) | `False` | fenced sparkline |
| Vistas | `False` | fenced table |
| Dispositivo / OS / Browser | `False` | fenced table |
| Ecrã · Idioma · País | `False` | fenced table |
| Referrers | `False` | fenced table |
| Caminhos | `False` | fenced table |
| Versões de OS | `False` | fenced table, optional |
| Versões de browser | `False` | fenced table, optional |
| Modelos | `False` | fenced table, optional |
| Hardware | `False` | fenced table, optional |

Six inline KPIs make exactly two rows of three — no orphan. The description carries only the period line; the numbers live in fields where Discord can lay them out, not in a fenced block that pretends to be a layout engine.

### Table width budget: 56 characters

Discord's **mobile client does not wrap inside a fenced block** — a wide table becomes a horizontal scroll on the one device this will actually be read on. Every rendered line therefore has a hard budget of **56 characters**, enforced by `render_table`, with values ellipsised (`…`) to fit rather than a single long value setting the width.

| Field | Columns (width) | Line |
|---|---|---|
| Tráfego diário | dia 10 · loads 6 · views 6 · sess 5 · únicos 7 | 38 |
| Atividade por hora | 24-slot sparkline + axis labels | 24 |
| Vistas | vista 8 · loads 6 · switch 7 · views 6 · % 5 | 36 |
| Dispositivo / OS / Browser | device 7 · os 8 · browser 16 · sess 5 · views 6 | 46 |
| Ecrã · Idioma · País | three stacked mini-lists, key 9 · sess 5 · % 5 | 21 |
| Referrers | referrer 34 · sess 5 · % 5 | 46 |
| Caminhos | caminho 38 · views 6 · sess 5 | 51 |
| Versões de OS | os 8 · versão 8 · sess 5 · % 5 | 28 |
| Versões de browser | browser 16 · versão 8 · sess 5 · % 5 | 36 |
| Modelos | model 34 · sess 5 · % 5 | 46 |
| Hardware | two mini-list pairs, key 9 · sess 5 | 32 |

Widest line is 51. `Samsung Internet` is exactly 16 and fixes the browser column; `model` is `VARCHAR(40)` and `referrer` `VARCHAR(100)`, so both are ellipsised to 34; `path` is `VARCHAR(255)`, ellipsised to 38, which still separates `/mapa` from `/mapa/pokemon/1380380506…`.

`Ecrã · Idioma · País` merges three short lists into one field — side by side they would need ~60 characters, so they are stacked with a blank line between, which keeps the field at 21 characters wide and saves two fields.

### Row caps

Caps exist to keep each field under 1 024 characters and the whole embed under the `_fits()` guard, not for legibility alone.

| Section | Cap | Note |
|---|---|---|
| Tráfego diário | 14 day rows | ≤ 14 days |
| Tráfego diário | 20 ISO-week rows | 15–140 days; Python rollup over the same query |
| Tráfego diário | 24 month rows | > 140 days; older months dropped with a `(últimos 24 meses)` note |
| Dispositivo / OS / Browser | 10 | |
| Referrers · Caminhos | 6 each | |
| Ecrã · Idioma · País | 6 · 6 · 5 | |
| Vistas | 4 | `view` is an enum of four |
| Optional fields | see the table in §11–14 | |

Worst case: 20 week rows × 39 chars ≈ 800 in the largest field, ≈ 4 900 characters across a fully-populated embed with all seven optional columns.

### The limits in play

| Limit | Value | How it is respected |
|---|---|---|
| Message content | 2 000 | Not in play: content is empty on the embed path, one line on the `.txt` path |
| Embed fields | 25 | 6 inline + 7 always-on blocks + 4 optional blocks = **17** worst case, 13 today |
| Field value | 1 024 | Row caps above; largest realistic field ≈ 800 |
| Field name | 256 | Short PT labels |
| Description | 4 096 | One period line |
| Embed total | 6 000 | Worst case ≈ 4 900; hard `_fits()` guard at 5 500 |
| Attachment (fallback) | 10 MB | A `.txt` of this report is under 10 KB |

### Recommendation

**One embed: six `inline=True` KPI fields, then one `inline=False` field per table whose value is a fenced code block rendered to the 56-character budget. If `_fits()` fails, send the identical report as a `webstats-<período>-<YYYY-MM-DD>.txt` attachment with a one-line embed instead.**

`_fits()` is a single pure predicate — field count ≤ 25, every value ≤ 1 024, total ≤ 5 500 — which makes the branch trivially testable. The guard is genuinely reachable, not decorative: `!webstats all` over a year, with all seven optional columns populated, lands near 4 900 and a cardinality surprise (dozens of phone models) pushes it over. When it does, the admin gets a complete report as a file rather than a truncated one or an `HTTPException`. No multi-message paging — a report split across four DMs is worse to read than one attachment.

### Sample DM (real data, `!webstats 7d`)

> **📊 Estatísticas do site**
> 2026-09-13 → 2026-09-19 · 7 dias · UTC · 5 dias com dados

| Loads | Views | Sessões |
|---|---|---|
| **67** | **109** | **67** |

| Views/sessão | Únicos/dia | PWA instalada |
|---|---|---|
| **1.63** | **9.4** média · **16** máx | **2** de 67 (3 %) |

*(the two tables above are the six `inline=True` fields as Discord lays them out)*

**Tráfego diário**
```
dia         loads  views  sess  únicos
2026-09-15      3      5     3       3
2026-09-16     23     41    23      16
2026-09-17     12     22    12      10
2026-09-18     26     37    26      15
2026-09-19      3      4     3       3
```
**Atividade por hora (UTC)**
```
0h                    23h
······▁▆▂▄▂▂█▃▅▄▃█▅▂▅▂▃▄
pico 12h · 14 views
```
**Vistas**
```
vista    loads  switch  views    %
map         26      30     56   51%
home        41       3     44   40%
quests       0       5      5    5%
dex          0       4      4    4%
```
**Dispositivo / OS / Browser**
```
device   os       browser           sess  views
mobile   Android  Chrome              38     63
desktop  Windows  Chrome              13     21
mobile   Android  Samsung Internet     7      9
desktop  Windows  Opera                4      7
mobile   iOS      Safari               3      6
tablet   Android  Chrome               1      2
desktop  Linux    Chrome               1      1
```
**Ecrã · Idioma · País**
```
380-429      48  72%
>=1280       19  28%

pt-PT        45  67%
en-US        15  22%
pt-BR         4   6%
en-GB         3   4%

PT           67 100%
```
**Referrers**
```
referrer                      sess    %
(direto)                        67 100%
```
**Caminhos**
```
caminho                            views  sess
/mapa                                 50    49
/                                     44    42
/quests                                5     5
/dex                                   4     3
/mapa/pokemon/13803805065508038…       1     1
```
> _visitor roda à meia-noite UTC — únicos só por dia; sessões para o período._

13 fields today. The four optional fields are absent because the `ALTER` has not run. An empty period sends the description `Sem dados no período.` with zero fields.

All labels are PT-PT, matching every other user-facing string in the bot. Embed colour is `Config.EMBED_COLOR` via `modules/embeds.build_embed`, which already sets the timestamp and accepts the footer.

## Rejected: a rendered HTML image

Rendering the report as an HTML page through the existing Jinja2 + `imgkit` pipeline (`modules/image_generator.py`, `templates/`) and attaching the PNG was considered and rejected. It would look better. It reads worse:

- **Text in an image is not selectable or copyable.** A number in this report is something to paste into a message, a query or a note. A PNG makes every one of them retyped by hand.
- **It adds a failure mode to a report that must work when the stack does not.** `_render_png` returns `None` on any `wkhtmltopdf` fault and the Google Fonts `@import` needs network at render time. A stats dump is exactly the thing an admin reaches for when something is already wrong; a text embed has no renderer to fail.
- **A fixed-width image does not reflow.** Discord scales an attachment to roughly 550 px in-chat, so a 900 px-wide report becomes pinch-and-zoom on a phone. Native fields reflow; a PNG cannot.
- The image path would also need a fourth template env var, and with it a `--force-recreate` deploy step that the embed design does not require.

The two existing templates stay exactly as they are.

## Config

**No new required environment variable.**

```python
# modules/config.py
DB_POGOLEIRIA = os.environ.get("DB_POGOLEIRIA", "pogoleiria")
```

| Var | Required | Default | Note |
|---|---|---|---|
| `DB_POGOLEIRIA` | no | `pogoleiria` | Same shape as `DB_DRAGONITE`. Not added to `_REQUIRED_ENV_VARS` — an unreachable analytics schema must not block boot |
| `MY_ID` | already set | — | DM recipient. `98846248865398784` in `.env` |
| `ADMIN_USERS_IDS` | already set | — | Not used by this cog — the gate is `MY_ID` |
| `MOD_CHANNEL_ID` | already set | — | DM-failure notice target |
| `DB_HOST/PORT/USER/PASSWORD` | already set | — | `pogoleiria`/`pogoleiria` already holds `GRANT ALL ON pogoleiria.*` |

Add `DB_POGOLEIRIA="pogoleiria"` to `.env.example` and `.env.test` for documentation. Because the default is correct, the feature works on a plain restart. **If `DB_POGOLEIRIA` is put into `.env`, the container must be recreated** — `env_file` is read only at container create:

```bash
cd /root/Poliswag && docker compose -f docker-compose.prod.yaml up -d --force-recreate poliswag
```

A `docker restart poliswag` keeps the stale environment.

No migration. `migrations/` gets no new file: the feature only reads, and it reads a schema owned by the landing app.

## Error handling

| Failure | Behaviour |
|---|---|
| Anyone but `MY_ID` invokes | Silent `return` from `cog_command_error`, nothing logged to the channel |
| Bad `período` | Usage embed in the invoking channel (`!webstats [1d\|7d\|30d\|Nd\|all]`), no DB connection, no DM, message not deleted |
| `MY_ID` unset (`0`) | Config-error embed in the channel, `_log` at ERROR, no DB connection |
| `pogoleiria` schema missing / DB down | Lazy connect raises → caught → error embed in the channel + `_log("[WEBSTATS] ...", "ERROR")`. The bot keeps running; no other feature is affected |
| Query fails mid-collect | `DatabaseConnector` already retries 3× and reconnects on errno 2006/2013; a final `RuntimeError`/`MySQLError` is caught by the cog and reported as above |
| Empty result set | Valid outcome, not an error: DM sent with `Sem dados no período.` and no section fields |
| Optional columns absent | Detection returns an empty set, none of the optional statements run, the four fields are absent. No `1054 Unknown column` is reachable |
| Optional column present, all `NULL` | `WHERE col IS NOT NULL` returns no rows → section dropped |
| `discord.Forbidden` on `user.send` (DMs closed, bot blocked) | `_log` at ERROR + one line to `MOD_CHANNEL`: `Não consegui enviar as estatísticas por DM (DMs fechadas?).` The statistics are **not** posted to the channel |
| `discord.NotFound` on `fetch_user` | Treated as a config error: `_log` + channel embed saying `MY_ID` is invalid |
| `discord.HTTPException` (payload rejected despite `_fits`) | `_log` at ERROR, retry once as the `.txt` attachment, then give up with a `MOD_CHANNEL` notice |
| `ctx.message.delete()` raises `Forbidden`/`NotFound` | Caught and ignored; the report still goes out |

Every `except` writes through `LoggingMixin._log`, i.e. `utility.log_to_file` → `logs/actions.log` + `logs/error.log`. No traceback ever reaches a Discord channel.

## Privacy

- The report aggregates; it never prints a `visitor` hash, a `load_id`, or a single row.
- No IP, no cookie, no full referrer URL, no user agent — none of those are in the table to begin with.
- `model` is the highest-entropy column. It is reported as a count per model with `LIMIT 8`, in a DM to the site owner only. At current volume a rare model is effectively one person, which is exactly why this surface is a private DM to `MY_ID` and not a public channel. The same reasoning covers `arch`, `bitness`, `cpu_cores` and `device_memory`: the report only ever prints one column at a time as a session count, never a joined per-device row, so it does not reassemble the fingerprint the sibling spec warns about.
- The feature adds no new collection, no retention change, and no new exposure: it reads what the landing app already stores under its own stated privacy stance (no cookies, no localStorage, no raw IP, daily-rotating `visitor`, no consent banner needed).
- The invoking message is deleted in guild channels, so the command itself leaves no trace in a shared channel.

## Testing

`pytest` 8.3.5 + `pytest-mock`, `asyncio_mode = "auto"`, coverage over `modules` and `cogs`. `tests/conftest.py`'s autouse `_prevent_real_db_connections` patches `pymysql.connect`, so nothing below touches a live DB or a Discord client.

### `tests/modules/test_page_view_stats.py`

- `resolve_period`: omitted → 7; `"1d"`, `"hoje"`, `"today"` → 1; `"7"`, `"7d"`, `"30d"` → 7/7/30; `"all"`, `"tudo"` → `None`; `"0d"`, `"400d"`, `"abc"`, `""` → `ValueError`.
- `since_for`: `days=None` → `1970-01-01`; `days=1` → today's UTC midnight; `days=7` → UTC midnight 6 days back; is computed from `datetime.now(timezone.utc)` (patched clock) and is **not** affected by a `TZ=Europe/Lisbon` environment.
- Every statement in the module: contains no `%` other than `%s`, and its `%s` count equals the params passed — guards the pymysql `query % args` trap.
- `collect` calls `get_data_from_database` once per statement with `params=(since,)` against a mocked connector, and returns a dict keyed by section.
- Optional columns: detection returning `set()` → no optional statement runs; `{"model"}` → only the `model` statement runs; all seven → all seven run. The detection statement passes `_OPTIONAL_COLUMNS` as a single tuple param, and a test asserts the tuple is exactly the seven names the sibling spec defines.
- The connector is created lazily: constructing `PageViewStats(poliswag)` performs no `DatabaseConnector(...)` call; the first `collect` does; the second reuses it.
- A `RuntimeError` from the connector propagates (the cog, not the service, decides what the user sees).

### `tests/modules/test_page_view_report.py` (pure, no Discord, no DB)

- Header math from fixture totals: views/session `1.63`; PWA share `3 %`; `sessions = 0` → no `ZeroDivisionError` and `—` rendered.
- Daily uniques: mean over days *with rows* (5 days → `9.4`), max `16`, and the day count is printed.
- Daily table: 14 days → 14 day rows; 15+ days → ISO-week rows with summed loads/views/sessions and a mean `únicos`; the rollup is order-stable.
- Sparkline: always exactly 24 characters; all-zero input → 24 `·`; the peak hour maps to `█`; a single non-zero hour is `█`.
- View split: percentages sum to 100 within rounding; `switches` = `views - loads`.
- Truncation: a 60-char `model`, a 90-char `path` and a 100-char `referrer` are cut to their column widths without breaking alignment.
- Column alignment: every rendered line of a table has the same length.
- Empty `stats` → `build_sections` returns `[]` and the embed carries `Sem dados no período.` with zero fields.
- Optional sections: absent from `build_sections` when their rows are empty; present with a coverage line when populated.
- Width budget: no line of any rendered table exceeds **56** characters, asserted over a fixture set that includes a 40-char `model`, a 100-char `referrer` and a 255-char `path`; each is ellipsised with `…` to its column width and the row still has the table's exact line length.
- Inline/block split: `build_dm_embed` produces exactly six `inline=True` fields in the documented order, and every other field is `inline=False` with a value that starts and ends with a fence.
- `build_dm_embed` on a synthetic worst case (365 days → 24 month rows, 10 device combos, all seven optional columns populated at their caps): ≤ 25 fields, every field value ≤ 1 024, total ≤ 6 000.
- Field count: 13 with no optional columns, 17 with all seven — the second asserts the four-fields-for-seven-columns grouping.
- `_fits` returns `False` for an oversized synthetic report, and `render_text_report` then produces the same section titles uncapped.
- Coverage line: an optional section whose sessions sum to 41 of a 67-session period renders `cobertura 41/67 sessões (61 %)`.

### `tests/cogs/test_webstats.py`

Following `tests/cogs/test_lures.py`: a `MagicMock` bot, `AsyncMock` services, commands invoked as `WebStats.webstats.callback(cog, ctx, ...)`.

- `cog_check`: `MY_ID` → `True`; an `ADMIN_USERS_IDS` member who is not `MY_ID` → `False`; a stranger → `False`.
- `cog_command_error` with `commands.CheckFailure` → returns without `ctx.send`; with another error → logs and sends an error embed.
- Bad period → usage embed via `ctx.send`, `collect` not called, `ctx.message.delete` not called, no DM.
- `Config.MY_ID = 0` → config-error embed, `collect` not called.
- Happy path in a guild: `ctx.message.delete` awaited once, `fetch_user` (or `get_user`) resolved to `MY_ID`, `user.send` awaited once with an embed, nothing sent to `ctx`.
- Happy path invoked in a `discord.DMChannel`: `ctx.message.delete` **not** called, DM still sent.
- `collect` raising → error embed in the channel, `_log` at ERROR, `user.send` not called.
- `user.send` raising `discord.Forbidden` → `MOD_CHANNEL.send` awaited with a notice that contains no statistics, `_log` at ERROR.
- `MOD_CHANNEL` is `None` → the `Forbidden` path only logs, no `AttributeError`.
- `build_dm_embed` returning `None` → `user.send` called with `file=` and a filename matching `webstats-*.txt`.
- Default period: calling with no argument collects with the 7-day `since`.
- `cog_load` / `cog_unload` print, and `setup` registers the cog — same three tests every cog test file has.

### Dev parity

`mock_database/init.sql` gains `CREATE DATABASE IF NOT EXISTS pogoleiria;` and a `page_view` table matching prod's current shape — **without** any of the seven optional columns, so `make up` exercises the missing-column path end to end — plus ~20 seed rows spanning three days, two devices, a `standalone = 1` session and a `NULL` referrer.

## Deploy

1. `modules/config.py`: add `DB_POGOLEIRIA`. `.env.example` + `.env.test`: document it. No `.env` change needed; if made, use `--force-recreate` (above).
2. New files: `modules/page_view_stats.py`, `modules/page_view_report.py`, `cogs/webstats.py`, `tests/modules/test_page_view_stats.py`, `tests/modules/test_page_view_report.py`, `tests/cogs/test_webstats.py`.
3. `main.py`: `self.page_view_stats = PageViewStats(self)` in `__init__`, `await self.load_extension("cogs.webstats")` in `setup_hook`.
4. `modules/help_command.py`: add the `WebStats` display name. `README.md`: add `!webstats [período] *(admin)*` under a new "Estatísticas" heading.
5. `mock_database/init.sql`: add the `pogoleiria` schema + seed rows.
6. `make check` (black + ruff + vulture + pytest) must pass.
7. Prod: `cd /root/Poliswag && docker compose -f docker-compose.prod.yaml restart poliswag`. A plain restart is enough: `.:/app` is bind-mounted so Python-only changes need no rebuild, and the feature introduces no *required* env var. `--force-recreate` is needed only if `DB_POGOLEIRIA` is pinned in `.env` (step 1).
8. Verify: `!webstats` in a guild channel → the message disappears and a DM arrives; `!webstats all` → the same report over `1970-01-01`; `!webstats 400d` → usage hint in the channel; run once from a non-`MY_ID` admin account and confirm the DM still lands on `MY_ID`.
9. After the landing app applies `002_page_view_device_detail.sql` (seven columns), re-run `!webstats 7d` and confirm the four optional fields appear, each with its coverage line, and that the embed still renders (17 fields).
