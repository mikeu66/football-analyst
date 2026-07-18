# MCP Server — Delegation Plan

Goal: a local stdio MCP server over `data/nfl.duckdb` so Claude Code and Claude
Desktop can answer fantasy/real-football questions conversationally. Scope is
the MCP server only (no Sleeper league integration or ADP sourcing yet).

Every task below is sized for delegation. Model labels:

- **Haiku 4.5** — mechanical, tightly specified, single-file work.
- **Sonnet 5** — design judgment, ambiguity, or correctness-sensitive work.
- **Opus (orchestrator)** — decomposition, review gates, integration, e2e.

Subagents must read `.claude/skills/nfl-data-context/SKILL.md` before touching
anything — it holds the schema facts, join-key gotcha, and code conventions.
The execution playbook lives in `.claude/skills/nfl-mcp-playbook/SKILL.md`.

## Layout decision (fixed — do not relitigate)

```
mcp_server/
  server.py            FastMCP app, stdio entrypoint, query + data_status tools
  db.py                read-only connection helper (fresh conn per query)
  semantics.md         curated schema/semantics doc, served by describe_data
  tools_players.py     player_lookup            — register(mcp) pattern
  tools_opportunity.py opportunity_gap          — register(mcp) pattern
  tools_market.py      trending, injury_report  — register(mcp) pattern
tests/
  test_mcp_server.py
```

One module per tool group so parallel subagents never edit the same file.
Each `tools_*.py` exposes `register(mcp: FastMCP) -> None`; `server.py` calls
each. Directory is `mcp_server/` (NOT `mcp/` — that would shadow the `mcp`
package import).

## Phase 0 — Foundations (sequential, blocking)

### T1. Server skeleton — **Sonnet 5**
- `uv add mcp` (FastMCP lives in `mcp.server.fastmcp`).
- `mcp_server/db.py`: `run_query(sql) -> list[dict]` opening a **fresh**
  `duckdb.connect("data/nfl.duckdb", read_only=True)` per call (the pipeline's
  wholesale table replaces conflict with long-lived readers).
- `mcp_server/server.py`: FastMCP app named `nfl-data`, stdio transport, two
  tools:
  - `query(sql)` — read-only escape hatch. Guardrails: single statement;
    first keyword in {SELECT, WITH, DESCRIBE, SHOW, SUMMARIZE}; row cap
    (LIMIT 200 appended if absent); errors returned as text, never raised.
  - `data_status()` — max season/week per table, row counts, file mtime as
    last-refresh proxy.
- **Gap found during execution (2026-07-16):** the layout section says
  `semantics.md` is "served by `describe_data`", but no task owned that tool —
  T1 specced only `query` + `data_status`. The orchestrator added
  `describe_data()` to `server.py` after T1's gate passed. Without it the
  semantic layer would have shipped unreachable by the LLM.
- **Acceptance:** `uv run python -c "from mcp_server.server import mcp"` works;
  a direct call of the two tool functions returns sane results; `query` rejects
  `INSERT`/multi-statement input with a helpful message.

### T2. Semantic layer — **Sonnet 5** (parallel-safe with T1)
- Write `mcp_server/semantics.md`: the ~30 columns that matter across
  `player_stats` (145 cols) and `ff_opportunity` (159 cols), plus the small
  tables. Must cover: the join key (see whitespace note below), `fantasy_points` vs
  `fantasy_points_ppr`, the `*_exp` / `*_diff` expected-points family and what
  the gap means (buy-low/sell-high), `sleeper_trending.kind` ∈ {add, drop} as
  a 24h window, schedules' Vegas columns (`spread_line`, `total_line`,
  moneylines), seasons loaded (2023–2025).
- Written **for an LLM consumer**: terse, factual, example joins included.
- **Acceptance:** Opus spot-checks 5 claims in the doc against live `DESCRIBE`
  / sample queries; all 5 must hold.

> **Correction (verified 2026-07-16, supersedes the skill's "critical" framing):**
> `sleeper_players.gsis_id` in the live DB is **already clean** — 0 of 3,893
> non-null values differ from `TRIM(gsis_id)`, and the join matches 22,407 rows
> with or without `TRIM`. `pipeline/sleeper.py:45-47` strips the value at ingest.
> The whitespace is an **upstream Sleeper API** quirk, normalized on the way in.
> `semantics.md` must describe it that way — do **not** tell the LLM the stored
> data is dirty. Keeping `TRIM()` in queries is fine as cheap defense against
> pipeline drift, but it is not required for correctness today.

## Phase 1 — Curated tools (parallel after T1+T2, all **Haiku 4.5**)

Each task: one file, `register(mcp)` pattern, uses `db.run_query`, reads
`semantics.md` conventions. Docstrings must state *when* to call the tool.

### T3. `tools_players.py` — `player_lookup(name)`
- Fuzzy name match (ILIKE both sources), returns gsis_id, Sleeper id, team,
  position, age, status, depth_chart_position. Handles the
  `TRIM(gsis_id)` join. Returns candidates list when ambiguous.

### T4. `tools_opportunity.py` — `opportunity_gap(position?, season?, last_n_weeks?)`
- Port `analysis/opportunity_gap.py` into a parameterized tool: expected vs
  actual PPR points, aggregated over the window, sorted by gap. Include
  per-game columns and games played.
- **Spec correction (orchestrator, 2026-07-16 — the original spec was wrong):**
  `last_n_weeks` must anchor to the last N **regular-season** weeks
  (`week <= 18`), not the last N weeks present. Weeks 19–22 are playoffs:
  verified week 20 has 8 teams, week 21 has 4, week 22 has 2. Anchoring to
  `MAX(week)=22` made `last_n_weeks=3` mean weeks 20–22 — a playoff-only
  sample excluding 30 of 32 teams, which is useless for a redraft fantasy
  question and silently misleading. `ff_opportunity` has **no `season_type`
  column**, so `week <= 18` is the only REG discriminator. The tool must also
  state the actual week range it used in its output.

### T5. `tools_market.py` — `trending(kind)` + `injury_report(team?, week?)`
- `trending`: join `sleeper_trending` → `sleeper_players` → nflverse ids;
  return name/team/position/count. `injury_report`: latest week by default,
  report + practice status.

### T6. Registration & docs — **Haiku 4.5**
- README section: run instructions, `claude mcp add nfl-data -- uv run python
  -m mcp_server.server` (verify exact syntax against `claude mcp add --help`),
  Claude Desktop `claude_desktop_config.json` snippet with absolute paths.

**Acceptance for T3–T5 (Opus runs after each):** import cleanly, direct call
returns correct data for a known case (e.g. a 2025 WR), ambiguous/empty inputs
return helpful text not exceptions.

## Phase 2 — Verification (sequential)

### T7. Test suite — **Sonnet 5**
- `tests/test_mcp_server.py` (pytest, add as dev dep): guardrail tests for
  `query` (rejects writes, multi-statement, respects row cap), one happy-path
  test per curated tool against the real DuckDB file, `data_status` shape test.
- **Acceptance:** `uv run pytest` green.

### T8. End-to-end over stdio — **Opus (orchestrator, do not delegate)**
- Register the server in this Claude Code session (or drive it with a minimal
  MCP client script), exercise every tool over the actual protocol, and answer
  one real analysis question end-to-end using only the server's tools.
- Fix-forward anything found; small fixes may be delegated back to Haiku.

## Dependency graph

```
T1 ──┬─▶ T3 ─┐
T2 ──┤   T4  ├─▶ T7 ─▶ T8
     └─▶ T5 ─┘
T6 (anytime after T1)
```
