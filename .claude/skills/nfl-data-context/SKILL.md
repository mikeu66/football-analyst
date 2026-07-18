---
name: nfl-data-context
description: Ground-truth schema, join keys, and code conventions for the NFL DuckDB dataset. Load before writing any code that touches data/nfl.duckdb — pipeline, analysis, app, or MCP server work.
---

# NFL Data Context

Ground truth for `/workspace/data/nfl.duckdb`. Facts below were verified
against the live database (July 2026, seasons 2023–2025 loaded). If something
here contradicts the database, trust the database and flag the discrepancy in
your final report.

## Project shape

- uv project, Python 3.12. Run everything as `uv run python ...`; add deps
  with `uv add <pkg>` (never pip).
- `pipeline/refresh.py` (nflverse via nflreadpy) and `pipeline/sleeper.py`
  (Sleeper API) rebuild the DuckDB file; `pipeline/db.py` is the shared
  writer. Refreshes **replace tables wholesale** — never hold a long-lived
  connection; open `duckdb.connect(path, read_only=True)` fresh per query.
- `analysis/opportunity_gap.py` is the worked buy-low query; `app/Home.py` is
  a Streamlit explorer.

## Tables (rows as of 2026-07)

| Table | Rows | What it is |
|---|---|---|
| `player_stats` | 57K | nflverse weekly stats, 145 cols, one row per player-week |
| `ff_opportunity` | 18K | ffverse expected fantasy points by week, 159 cols |
| `players` | 25K | nflverse player master (ids: gsis, pfr, espn, sleeper-adjacent) |
| `snap_counts` | 80K | per-game snap counts + percentages (offense/defense/st) |
| `injuries` | 18K | weekly injury reports (report + practice status) |
| `schedules` | 855 | games incl. Vegas: `spread_line`, `total_line`, `away_moneyline`, `home_moneyline` |
| `sleeper_players` | 12K | Sleeper player dump (age, status, `depth_chart_position`, injury fields) |
| `sleeper_trending` | 200 | 24h most added/dropped: `player_id`, `kind` ∈ {'add','drop'}, `count` |

## The join key (critical)

nflverse `player_id` ↔ `sleeper_players.gsis_id` — GSIS IDs like
`00-0033873`:

```sql
JOIN sleeper_players sp ON sp.gsis_id = ps.player_id
```

**⚠ This join is correct but SPARSE — it silently drops most of Sleeper.**
Verified 2026-07-16: `sleeper_players.gsis_id` is NULL for **8,307 of 12,200
rows (68%)**. Among *active* QB/RB/WR/TE (n=2,809): only 29% have a `gsis_id`,
43% have an `espn_id`, and **56% have neither**. The gaps include stars —
Justin Jefferson has no `gsis_id` (bridge via `espn_id`), and Ja'Marr Chase has
neither, though he is in nflverse as `00-0036900`. **Never conclude "player not
found" from a failed gsis join.** Fall back: `gsis_id` → `espn_id` (cast:
Sleeper stores it BIGINT, nflverse VARCHAR) → `(full_name, position)` — name
*plus* position, never name alone (two distinct Justin Jeffersons exist: MIN WR
and CLE LB). See `mcp_server/tools_players.py` for a working implementation.

**Team codes differ by source:** Sleeper uses `LAR`/`OAK`; nflverse tables use
`LA`/`LV`; `players.latest_team` contains both `ARI` and `AZ`. Filtering an
nflverse table by `'LAR'` returns zero rows silently. Normalize first (see
`TEAM_ALIASES` in `mcp_server/tools_market.py`).

**On whitespace:** older versions of this file said Sleeper's `gsis_id` has
stray whitespace and to always `TRIM()`. That is **stale** — `pipeline/sleeper.py`
strips it at ingest; verified 0 of 3,893 non-null values are untrimmed and the
join matches identically with or without `TRIM`. The column that *is* dirty is
`injuries.practice_status` (69 rows hold a literal `"\n    "`), and plain
`TRIM()` will **not** clean it — DuckDB's `TRIM` strips spaces, not newlines.
Use `NULLIF(TRIM(practice_status, E' \t\r\n'), '')`.

`sleeper_trending.player_id` is a **Sleeper** id — join to
`sleeper_players.player_id` first, then bridge to nflverse via gsis_id.

Fuller, verified detail lives in `mcp_server/semantics.md`.

## Fantasy conventions

- `player_stats.fantasy_points` = standard, `fantasy_points_ppr` = PPR.
  Default to PPR (user plays redraft PPR-style leagues).
- `ff_opportunity` families: `<cat>_fantasy_points` (actual),
  `<cat>_fantasy_points_exp` (expected from opportunity model),
  `<cat>_fantasy_points_diff` (actual − expected), for `pass`/`rec`/`rush`
  and `total_*`. **Negative diff with high `_exp` = buy-low candidate;
  positive diff = possible regression / sell-high.**
- `ff_opportunity` join keys: `season`, `week`, `player_id` (gsis).

## Code conventions

- Match existing style in `pipeline/` and `analysis/` (plain functions, no
  classes unless warranted, minimal comments).
- SQL: uppercase keywords, explicit column lists over `SELECT *` (the wide
  tables make `*` unusable downstream).
- Return query results as `list[dict]` (`cur.fetchall()` zipped with
  description, or `.pl().to_dicts()`), never DataFrames across MCP tool
  boundaries — tool output must be JSON-serializable.
- Errors in MCP tools: return a short explanatory string; never let an
  exception escape the tool function.

## MCP server specifics (when working on `mcp_server/`)

- FastMCP: `from mcp.server.fastmcp import FastMCP`. The package dir is
  `mcp_server/`, never `mcp/` (import shadowing).
- Tool modules expose `register(mcp: FastMCP) -> None` and are registered in
  `server.py`. Touch only your assigned module.
- Tool docstrings are read by an LLM to decide when to call the tool — state
  the trigger condition ("Call this when the user asks who to add off
  waivers…"), the parameters, and what the output means.
- Full plan and acceptance criteria: `docs/mcp-server-plan.md`.
