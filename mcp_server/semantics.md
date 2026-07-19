# NFL DuckDB — semantics

Curated map of `data/nfl.duckdb` for an LLM that cannot see the schema
directly. `player_stats` has 145 columns and `ff_opportunity` has 159 — this
doc lists only the ones worth querying. Use the `query` tool's `DESCRIBE
<table>` for anything not covered here.

Seasons loaded: **2023, 2024, 2025**, all complete (every `schedules` row has
a final score; no in-progress season). Every weekly table mixes regular
season and playoffs, but they mark the split differently:
- `player_stats.season_type`: `REG` (weeks 1–18, 54,473 rows) / `POST` (weeks
  19–22, 2,572 rows).
- `ff_opportunity` has **no `season_type` column** — playoffs are simply
  `week >= 19`. Regular season = `WHERE week <= 18`.
- `schedules.game_type`: finer-grained — `REG` (816), `WC` (18), `DIV` (12),
  `CON` (6), `SB` (3).

**⚠ Always filter to regular season unless the user asks about playoffs**
(`season_type = 'REG'` on `player_stats`, `week <= 18` on `ff_opportunity`).
An unfiltered season aggregate silently includes playoff games — the totals
look plausible and are wrong, and they won't line up with regular-season
numbers from other tables or tools (e.g. a 17-game player shows 18+ "games").

Row counts (2026-07): `player_stats` 57,045 · `ff_opportunity` 18,140 ·
`players` 25,033 · `snap_counts` 79,767 · `injuries` 17,882 · `schedules` 855
· `sleeper_players` 12,200 · `sleeper_trending` 200 · `ffc_adp` 212.

## The join key

nflverse uses GSIS ids (`00-0033873`) as the player identifier in
`player_stats`, `ff_opportunity`, `players.gsis_id`, `injuries.gsis_id`.
Sleeper uses its own numeric-string `player_id` in `sleeper_players` and
`sleeper_trending`, and separately stores the GSIS id in
`sleeper_players.gsis_id` as a bridge column.

```sql
JOIN sleeper_players sp ON sp.gsis_id = ps.player_id   -- ps = player_stats or ff_opportunity
```

### ⚠ The gsis bridge is SPARSE — this is the biggest trap in this database

**`sleeper_players.gsis_id` is NULL for 8,307 of 12,200 rows (68%).** The join
above is *correct* but *silently lossy*: it drops two thirds of Sleeper, and
the missing players are not just noise — they include stars.

Verified coverage for **active, fantasy-relevant** Sleeper players
(`status='Active' AND position IN ('QB','RB','WR','TE')`, n=2,809):

| bridge available | count | share |
|---|---|---|
| `gsis_id` | 826 | 29% |
| `espn_id` | 1,218 | 43% |
| either one | 1,222 | 44% |
| **neither** | **1,587** | **56%** |

Concrete, verified: **Justin Jefferson** (Sleeper `6794`) has NULL `gsis_id`
but a usable `espn_id` (4262921 → gsis `00-0036322`). **Ja'Marr Chase**
(Sleeper `7564`) has NULL `gsis_id` **and** NULL `espn_id`, yet he is present
in nflverse as `00-0036900`. A gsis-only join loses both.

**Never conclude "player not found" from a failed gsis join.** Fall back:

1. `gsis_id` — exact, cleanest, but only ~29% of active skill players.
2. `espn_id` — **type mismatch**: `sleeper_players.espn_id` is `BIGINT`,
   `players.espn_id` is `VARCHAR`. Cast both sides:
   `ON CAST(p.espn_id AS VARCHAR) = CAST(sp.espn_id AS VARCHAR)`.
3. `(full_name, position)` — the only bridge for the remaining ~56%.
   Verified near-unique: only **8** duplicate `(full_name, position)` groups
   among active Sleeper skill players, and **0** among nflverse `players` with
   `last_season >= 2023`. Match on name **+ position** — never name alone:
   there are two distinct *Justin Jeffersons* (MIN WR and CLE LB).

This sparsity also means `analysis/opportunity_gap.py`'s
`LEFT JOIN ... ON t.gsis_id = g.player_id` silently reports 0 Sleeper adds for
any trending player lacking a gsis_id.

### Team codes differ between sources

Verified distinct values:

| column | codes | notes |
|---|---|---|
| `injuries.team`, `player_stats.team`, `schedules.*_team`, `snap_counts.team` | 32 | uses **`LA`** (Rams), `ARI`, `LV`, `JAX`, `WAS` |
| `sleeper_players.team` | 33 | uses **`LAR`**, and still carries legacy **`OAK`** |
| `players.latest_team` | 33 | uses `LA`, but contains **both `ARI` and `AZ`** |

So `sleeper_players.team = 'LAR'` and `injuries.team = 'LA'` are the same team.
Filtering nflverse tables by `'LAR'` returns **zero rows, silently**. Normalize
before filtering: `LAR`/`STL` → `LA`, `OAK` → `LV`, `SD` → `LAC`, `AZ`/`ARZ` →
`ARI`, `WSH` → `WAS`, `JAC` → `JAX`. (`tools_market.py` does this via
`TEAM_ALIASES`.) A player's team from `player_lookup` comes from Sleeper, so it
may need this mapping before it is used against any nflverse table.

**On the whitespace note in the skill file:** it is stale. Verified live:
0 of 3,893 non-null `sleeper_players.gsis_id` values differ from
`TRIM(gsis_id)`, and the join above returns the identical 22,407 rows with or
without `TRIM()`. `pipeline/sleeper.py` strips the value at ingest — Sleeper's
raw API response has leading whitespace on some `gsis_id` values, but the
*stored* data is clean. `TRIM()` is harmless defensive hygiene against future
pipeline drift, not something required for correctness today. Don't tell a
user or downstream tool the stored ids are dirty — they aren't.

`sleeper_trending.player_id` is a **Sleeper** id, not GSIS — it will not match
`player_stats.player_id` directly. Bridge through `sleeper_players` first:

```sql
SELECT st.kind, sp.full_name, sp.gsis_id
FROM sleeper_trending st
JOIN sleeper_players sp ON sp.player_id = st.player_id   -- Sleeper id -> Sleeper id
-- then optionally: JOIN player_stats ps ON ps.player_id = sp.gsis_id
```
Verified: this bridge resolves real names for both `add` and `drop` rows
(e.g. sleeper id `1166` → Kirk Cousins, gsis `00-0029604`).

`snap_counts` has **no gsis_id column at all** — it carries `pfr_player_id`
(Pro-Football-Reference id) instead. Bridge via `players.pfr_id`:
```sql
JOIN players p ON p.pfr_id = sc.pfr_player_id
```
Verified match rate: 79,655 / 79,767 rows (99.86%) resolve through this
bridge; a small tail of snap_counts rows have no PFR id match in `players`.

`player_stats` ↔ `ff_opportunity` join keys are `player_id` + `season` +
`week`, **but the column types differ across the two tables** —
`ff_opportunity.season` is `VARCHAR`, `player_stats.season` is `INTEGER`;
`ff_opportunity.week` is `DOUBLE`, `player_stats.week` is `INTEGER`.

DuckDB casts these implicitly, so the naive join **works** — verified: joining
without any `CAST` returns 16,860 rows, identical to the explicit-cast version.
Filters behave the same way: `WHERE season = 2025` (int literal) and
`WHERE season = '2025'` (string literal) both return 6,054 rows on
`ff_opportunity`. You do not need to cast for correctness.

Casting explicitly is still good hygiene — it documents the type drift and
survives a stricter engine:
```sql
JOIN ff_opportunity fo
  ON fo.player_id = ps.player_id
 AND CAST(fo.season AS INTEGER) = ps.season
 AND CAST(fo.week AS INTEGER) = ps.week
```
Both forms produce the same 16,860 matching rows.

One real consequence of the VARCHAR type: `MAX(season)` on `ff_opportunity`
returns the VARCHAR `'2025'` and compares lexicographically. Harmless for
4-digit years, but don't assume it's numeric.

## `player_stats` — key columns

Identity: `player_id` (gsis), `player_name`, `player_display_name`, `position`,
`position_group`, `season`, `week`, `season_type`, `team`, `opponent_team`,
`game_id`.

Passing: `completions`, `attempts`, `passing_yards`, `passing_tds`,
`passing_interceptions`, `passing_epa`, `passing_cpoe`.

Rushing: `carries`, `rushing_yards`, `rushing_tds`, `rushing_epa`.

Receiving: `receptions`, `targets`, `receiving_yards`, `receiving_tds`,
`receiving_epa`, `target_share`, `air_yards_share`, `wopr` (weighted
opportunity rating — combines target share and air yards share).

Fantasy: `fantasy_points` (standard scoring) vs **`fantasy_points_ppr`**
(PPR — full point per reception). **Default to `fantasy_points_ppr`** — this
user plays redraft PPR leagues.

`player_stats` covers every position including IDP/special-teams (`LB`, `CB`,
`DT`, `K`, `P`, `LS`, ...) — not just fantasy-relevant skill positions.
Verified position breakdown includes `LB` (7,871 rows, the largest group).

## `ff_opportunity` — key columns

Identity: `player_id` (gsis), `full_name`, `position`, `posteam`, `season`
(VARCHAR), `week` (DOUBLE), `game_id`.

**⚠ No `season_type` column — add `week <= 18` to every regular-season
aggregate.** Weeks 19–22 are playoffs. Forgetting the filter inflates season
totals for playoff teams' players and skews any actual-vs-expected comparison
against regular-season stats from `player_stats` or other tools.

Only real fantasy-relevant positions have meaningful volume: verified
breakdown is WR 6,798 / RB 4,428 / TE 3,423 / QB 2,105, plus small noise counts
for P/DB/OL/LB/DL/K (≤35 rows each, artifacts of the source model — ignore).

### The `_exp` / `_diff` family

For each category `pass`, `rec`, `rush`, and the position-agnostic `total`:
- `<cat>_fantasy_points` — actual fantasy points scored from that activity.
- `<cat>_fantasy_points_exp` — **expected** fantasy points, from a model of
  the opportunity given (targets, air yards, red-zone touches, etc.) — what
  an average player would be expected to score given the same usage.
- `<cat>_fantasy_points_diff` — **actual − expected**. Verified arithmetically
  on live rows, e.g. Sam Howell week 1 2023: `rush_fantasy_points=7.1`,
  `rush_fantasy_points_exp=3.56`, `rush_fantasy_points_diff=3.54` (matches
  within float rounding).

**What the gap means:**
- **Negative diff + high `_exp`** → player is being given real opportunity but
  under-converting it (drops, bad luck, TD regression due) → **buy-low**
  candidate; expect positive regression toward the opportunity-implied rate.
- **Positive diff** → player is outperforming their opportunity (efficient but
  possibly unsustainable, e.g. inflated TD rate) → **sell-high** / regression
  candidate.

Verified example (2025 season, WR, ≥5 games, sorted by ascending
`total_fantasy_points_diff`) — all strongly negative, i.e. classic buy-low
reads: Justin Jefferson (`actual=201.5, expected=249.1, diff=-47.6`, 17
games), Davante Adams (`258.4` vs `301.7`, `diff=-43.3`).

The same `_exp`/`_diff` columns also exist with a `_team` suffix
(`pass_fantasy_points_team`, etc.) — these are team-level totals for the same
categories, not per-player; skip them unless doing team-level analysis.

## `players` — nflverse master

Key columns: `gsis_id` (join key), `display_name`, `position`,
`position_group`, `latest_team`, `status` (roster status code — verified
values: `ACT`, `CUT`, `RES`, `DEV`, `RSN`, `NWT`, `PUP`, `RSR`, `SUS`, `RET`,
not human-readable strings), `rookie_season`, `last_season`,
`years_of_experience`, `draft_year`/`draft_round`/`draft_pick`/`draft_team`,
`height`, `weight`, `college_name`. Also carries bridge ids: `pfr_id`,
`espn_id`, `nfl_id`, `smart_id` — useful for joining to `snap_counts` (via
`pfr_id`) or other sources.

## `snap_counts`

`game_id`, `season`, `week`, `game_type`, `player` (name string),
`pfr_player_id` (join to `players.pfr_id`, see above — no gsis_id column
here), `position`, `team`, `opponent`, `offense_snaps`/`offense_pct`,
`defense_snaps`/`defense_pct`, `st_snaps`/`st_pct` (special teams).
`*_pct` columns are fractions (0.14 = 14%), not already-multiplied percents —
verified from live rows (`offense_pct=0.14` alongside `offense_snaps=1.0` of
`71` team plays).

## `injuries`

`season`, `season_type`, `week`, `team`, `gsis_id` (join key, present
directly — no bridge needed), `position`, `full_name`,
`report_primary_injury`/`report_secondary_injury`, `report_status`,
`practice_primary_injury`/`practice_secondary_injury`, `practice_status`,
`date_modified` (timestamptz).

`report_status` ∈ {`Questionable`, `Out`, `Doubtful`, `Note`, NULL} — verified
distribution: `Questionable` 4,377, `Out` 3,508, `Doubtful` 442, `Note` 6,
NULL 9,549 (no report filed that week, not "healthy" — absence of a row/NULL
does not mean the player was fully healthy, just unreported).

`practice_status` is mostly descriptive prose (`Full Participation in
Practice`, `Did Not Participate In Practice`, `Limited Participation in
Practice`) but **verified 69 rows contain a whitespace-only value** — the
literal 5-char string `"\n    "` (newline + 4 spaces) instead of NULL or a
real status. Plus 45 genuine NULLs. Unlike `gsis_id`, this column really is
dirty.

**Do not use plain `TRIM()` to clean it.** DuckDB's `TRIM(str)` strips
*spaces only*, not newlines — it turns `"\n    "` into `"\n"`, which is still
truthy and still not a valid status, so the bug survives the fix. Verified: 0
of the 69 rows are caught by `TRIM(practice_status) = ''`. Use an explicit
character set or a regex:
```sql
-- treat blank-ish practice_status as unknown
NULLIF(TRIM(practice_status, E' \t\r\n'), '')          -- explicit char set
-- or detect them:
WHERE regexp_matches(practice_status, '^\s*$')          -- catches all 69
```

## `schedules`

Identity: `game_id`, `season`, `game_type`, `week`, `gameday`, `away_team`,
`home_team`, `away_score`, `home_score`. Convenience columns (verified,
computed from the scores, present once game is final): `result` =
`home_score − away_score`; `total` = `home_score + away_score`.

Vegas columns: `spread_line`, `total_line`, `away_moneyline`,
`home_moneyline`, `away_spread_odds`, `home_spread_odds`, `over_odds`,
`under_odds`.

**`spread_line` sign convention (verified empirically, do not assume the
usual American-odds sign):** `spread_line` is signed **relative to the home
team**, and **positive means the home team is favored** by that many points;
negative means the home team is the underdog. This is the *opposite* of how
a single favorite's own spread is usually quoted (e.g. "-7.5" for the
favorite in sportsbook shorthand).
- Verified: `2024_18_CLE_BAL` (away=CLE, home=BAL), `spread_line=19.5`,
  `home_moneyline=-2400` (home heavily favored), home won 35–10
  (`result=25`, exceeding the spread).
- Verified: `2024_15_BAL_NYG` (away=BAL, home=NYG), `spread_line=-16.5`,
  `home_moneyline=+1000` (home underdog), home lost 14–35
  (`result=-21`, worse than the spread).
- Verified across all 855 games: `corr(spread_line, result) = 0.47` (positive
  — spread and actual home margin move together, confirming the sign is
  home-relative, not away-relative or magnitude-only).

`total_line` is the pregame over/under on `total` (combined score); it is a
market prediction, not derived from `total` — verified they diverge
(`avg(total - total_line) ≈ +1.1` across all games, as expected for a market
line).

Moneylines (`away_moneyline`, `home_moneyline`) use standard American odds:
negative = favorite, positive = underdog — verified consistent with the
`spread_line` examples above (BAL `-2400` favorite matches its `+19.5` home
spread favorite status).

## `sleeper_players`

`player_id` (Sleeper id), `gsis_id` (bridge to nflverse), `full_name`,
`position`, `fantasy_positions` (comma-joined string, e.g. multi-eligible
players), `team`, `age`, `years_exp`, `status` (Sleeper's own vocabulary —
verified values `Active`, `Inactive`, `Injured Reserve`, `Physically Unable
to Perform`, `Non Football Injury`, `Practice Squad`, NULL — **different
vocabulary from `players.status`**, don't conflate the two), `injury_status`,
`injury_body_part`, `depth_chart_position` (verified granular, e.g. `LOLB`,
`LCB`, `NB`, `SS`, not just base positions), `depth_chart_order` (1 = starter).

## `sleeper_trending`

`player_id` (Sleeper id — bridge via `sleeper_players`, see above), `kind` ∈
{`add`, `drop`} (verified: exactly 100 rows each), `count` (number of Sleeper
managers who added/dropped in the window). This is a point-in-time snapshot
of Sleeper's trending endpoint, refreshed by the pipeline each run; Sleeper
documents it as a rolling 24-hour window — the table itself carries no
timestamp column, so "as of last refresh" is the only freshness signal
available (see `data_status()` tool for refresh time).

## `ffc_adp`

Current-year PPR ADP from Fantasy Football Calculator (real mock/live
drafts, rolling ~1-week window), refreshed by `pipeline/adp.py`. One row per
drafted player, 12-team leagues.

Columns: `ffc_id` (FFC's own id), `name`, `position` (∈ {QB, RB, WR, TE,
`PK`, `DEF`} — note **`PK` not `K`**, and `DEF` = team defense), `team`
(**Sleeper-style codes**: `LAR`, not `LA` — normalize before joining nflverse
tables), `adp` (average overall pick, e.g. `1.6`), `adp_formatted`
(`round.pick`, e.g. `"1.02"`), `times_drafted`, `high`/`low` (best/worst pick
observed), `stdev`, `bye`, and **`gsis_id`** — bridged at ingest via
name+position matching against `players` (verified 193/193 skill players
matched for 2026; NULL for `DEF` rows). Snapshot metadata repeats on every
row: `year`, `scoring` (`'ppr'`), `league_teams`, `total_drafts`,
`window_start`/`window_end` (dates of the draft window).

Join directly on `gsis_id`:
```sql
JOIN ff_opportunity fo ON fo.player_id = a.gsis_id   -- a = ffc_adp
```
The `adp_value` tool already implements the standard "current ADP vs. last
season's expected points" value analysis — prefer it over hand-rolling SQL.

## Example queries (all verified to run)

Buy-low WR candidates, 2025 season, min 5 games:
```sql
SELECT fo.full_name,
       SUM(fo.total_fantasy_points)      AS actual,
       SUM(fo.total_fantasy_points_exp)  AS expected,
       SUM(fo.total_fantasy_points_diff) AS diff,
       COUNT(*) AS games
FROM ff_opportunity fo
WHERE fo.position = 'WR' AND fo.season = '2025'
GROUP BY fo.full_name
HAVING COUNT(*) >= 5
ORDER BY diff ASC
LIMIT 10;
```

Player lookup bridging nflverse and Sleeper:
```sql
SELECT p.display_name, p.position, p.latest_team,
       sp.player_id AS sleeper_id, sp.status, sp.depth_chart_position
FROM players p
JOIN sleeper_players sp ON sp.gsis_id = p.gsis_id
WHERE p.display_name ILIKE '%jefferson%';
```

Trending adds resolved to real names:
```sql
SELECT st.kind, sp.full_name, sp.team, sp.position, st.count
FROM sleeper_trending st
JOIN sleeper_players sp ON sp.player_id = st.player_id
WHERE st.kind = 'add'
ORDER BY st.count DESC
LIMIT 10;
```

Snap share via the PFR bridge:
```sql
SELECT p.display_name, sc.season, sc.week, sc.offense_pct
FROM snap_counts sc
JOIN players p ON p.pfr_id = sc.pfr_player_id
WHERE p.display_name ILIKE '%mccaffrey%'
ORDER BY sc.season, sc.week;
```

Vegas context for a team's upcoming/played games (remember: positive
`spread_line` = home favored):
```sql
SELECT game_id, away_team, home_team, spread_line, total_line,
       away_moneyline, home_moneyline
FROM schedules
WHERE season = 2025 AND (home_team = 'KC' OR away_team = 'KC')
ORDER BY week;
```
