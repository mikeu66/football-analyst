"""Player lookup tool for the NFL MCP server.

Bridges the two player-identity sources (nflverse `players` and Sleeper
`sleeper_players`) into a single row per real human being.

See `semantics.md`, section "The gsis bridge is SPARSE", for why this is
hard: `sleeper_players.gsis_id` is NULL for ~68% of rows, so a naive
`FULL OUTER JOIN ... ON sp.gsis_id = p.gsis_id` fragments a single player
into two half-rows (one from each source) whenever Sleeper's gsis_id is
missing. This tool resolves each Sleeper row to a gsis_id through a
fallback chain before joining, so a fantasy-relevant player like Justin
Jefferson (bridged via espn_id) or Ja'Marr Chase (bridged via
full_name + position, since he has neither gsis_id nor espn_id on the
Sleeper side) comes back as one row, not two.

Fallback chain (in priority order):
  1. `gsis_id` — exact, cleanest, but only ~29% of active skill players.
  2. `espn_id` — cast to VARCHAR on both sides (Sleeper stores it BIGINT,
     nflverse stores it VARCHAR).
  3. `(full_name, position)` — verified near-unique; matching on name
     *alone* would incorrectly merge the two distinct Justin Jeffersons
     (MIN WR and CLE LB).

The `matched_via` column reports which rung of the chain resolved each
row (`gsis` / `espn` / `name_position` / `sleeper_only` / `nflverse_only`)
so a name-based heuristic match is never silently presented as certain.
"""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import FastMCP

from mcp_server.db import run_query

logger = logging.getLogger(__name__)

CANDIDATE_LIMIT = 15
MAX_NAME_LENGTH = 200

_SQL = """
WITH sleeper_clean AS (
    -- Sleeper's own data carries ~45 literal placeholder rows named
    -- 'Duplicate Player' or '<Name> DUPLICATE' -- verified: some of these
    -- carry a real player's espn_id (e.g. player_id 5308, full_name
    -- 'Duplicate Player', espn_id 3120464 -- the same espn_id as the real
    -- John Franklin-Myers). Left in, they bridge onto a real player and
    -- resurrect the exact half-row duplication this tool exists to fix.
    -- They are not real people; drop them before any bridging.
    SELECT * FROM sleeper_players
    WHERE full_name NOT ILIKE '%duplicate%'
),
gsis_direct AS (
    SELECT player_id AS sleeper_player_id, gsis_id AS matched_gsis
    FROM sleeper_clean
    WHERE gsis_id IS NOT NULL
),
espn_bridge AS (
    SELECT sp.player_id AS sleeper_player_id, p.gsis_id AS matched_gsis
    FROM sleeper_clean sp
    JOIN players p
      ON sp.espn_id IS NOT NULL
     AND p.espn_id IS NOT NULL
     AND CAST(p.espn_id AS VARCHAR) = CAST(sp.espn_id AS VARCHAR)
    QUALIFY ROW_NUMBER() OVER (PARTITION BY sp.player_id ORDER BY p.gsis_id) = 1
),
name_position_bridge AS (
    -- Verified near-unique on (full_name, position) among nflverse players
    -- with last_season >= 2023 (0 duplicate groups) -- see semantics.md.
    -- Name + position together, never name alone (there are two distinct
    -- "Justin Jefferson"s: MIN WR and CLE LB).
    SELECT sp.player_id AS sleeper_player_id, p.gsis_id AS matched_gsis
    FROM sleeper_clean sp
    JOIN players p
      ON p.display_name = sp.full_name
     AND p.position = sp.position
     AND p.last_season >= 2023
    QUALIFY ROW_NUMBER() OVER (PARTITION BY sp.player_id ORDER BY p.gsis_id) = 1
),
resolved_sleeper AS (
    SELECT
        sp.player_id AS sleeper_player_id,
        sp.full_name,
        sp.position,
        sp.team,
        sp.age,
        sp.status AS sleeper_status,
        sp.depth_chart_position,
        sp.depth_chart_order,
        COALESCE(gd.matched_gsis, eb.matched_gsis, nb.matched_gsis) AS resolved_gsis,
        CASE
            WHEN gd.matched_gsis IS NOT NULL THEN 'gsis'
            WHEN eb.matched_gsis IS NOT NULL THEN 'espn'
            WHEN nb.matched_gsis IS NOT NULL THEN 'name_position'
            ELSE 'sleeper_only'
        END AS matched_via
    FROM sleeper_clean sp
    LEFT JOIN gsis_direct gd ON gd.sleeper_player_id = sp.player_id
    LEFT JOIN espn_bridge eb ON eb.sleeper_player_id = sp.player_id
    LEFT JOIN name_position_bridge nb ON nb.sleeper_player_id = sp.player_id
),
joined AS (
    SELECT
        COALESCE(p.display_name, r.full_name)   AS name,
        COALESCE(p.gsis_id, r.resolved_gsis)    AS gsis_id,
        r.sleeper_player_id,
        COALESCE(r.team, p.latest_team)         AS team,
        COALESCE(p.position, r.position)        AS position,
        r.age,
        p.status                                AS nflverse_status,
        r.sleeper_status,
        r.depth_chart_position,
        r.depth_chart_order,
        COALESCE(r.matched_via, 'nflverse_only') AS matched_via,
        -- Dedup key: real identity when resolved, else fall back to a
        -- per-row unique key so unmatched rows are never grouped together
        -- (PARTITION BY NULL would otherwise merge every unmatched row).
        COALESCE(
            p.gsis_id, r.resolved_gsis,
            'sleeper:' || r.sleeper_player_id, 'nflverse:' || p.gsis_id
        ) AS dedup_key,
        CASE COALESCE(r.matched_via, 'nflverse_only')
            WHEN 'gsis' THEN 0 WHEN 'espn' THEN 1
            WHEN 'name_position' THEN 2 WHEN 'sleeper_only' THEN 3
            ELSE 4
        END AS match_rank
    FROM resolved_sleeper r
    FULL OUTER JOIN players p ON r.resolved_gsis = p.gsis_id
    WHERE COALESCE(p.display_name, '') ILIKE '{name_filter}'
       OR COALESCE(r.full_name, '') ILIKE '{name_filter}'
)
SELECT name, gsis_id, sleeper_player_id, team, position, age,
       nflverse_status, sleeper_status, depth_chart_position, matched_via
FROM joined
-- A handful (verified: 16) of gsis_ids are targeted by *two* genuinely
-- distinct sleeper_players rows for the same real person (Sleeper itself
-- carries duplicate entries, e.g. two separate ids both named "Carlos
-- Davis" DT). Collapse those to one row: prefer the tighter match tier,
-- then an Active Sleeper status, then a populated depth chart slot.
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY dedup_key
    ORDER BY match_rank,
             CASE WHEN sleeper_status = 'Active' THEN 0 ELSE 1 END,
             depth_chart_order NULLS LAST,
             sleeper_player_id
) = 1
-- Relevance order. Two distinct humans can share a name AND a position AND an
-- Active status AND depth_chart_order=1 (verified: Justin Jefferson the MIN WR
-- and Justin Jefferson the CLE LB), so depth chart alone cannot rank them and
-- the wrong one surfaced first. nflverse issues two id formats: canonical
-- '00-XXXXXXX' and synthetic ('JEF270909'). Verified: synthetic-id players have
-- 0 rows in player_stats (vs 56,979 for canonical) -- they have never recorded
-- a snap. So a canonical id means "this player actually plays".
ORDER BY
    CASE WHEN lower(name) = lower('{exact_name}') THEN 0 ELSE 1 END,
    CASE WHEN gsis_id LIKE '00-%' THEN 0 ELSE 1 END,
    CASE WHEN sleeper_status = 'Active' THEN 0 ELSE 1 END,
    depth_chart_order NULLS LAST,
    name
LIMIT {limit}
"""


def player_lookup(name: str) -> str:
    """Look up a player by name to resolve their IDs, team, position, and depth-chart role.

    Call this when the user names a player and you need their gsis_id, Sleeper
    player_id, team, position, age, or depth-chart position, or to resolve an
    ambiguous/partial name before querying other tools.

    Bridges the nflverse `players` table and Sleeper's `sleeper_players` table
    through a fallback chain (gsis_id, then espn_id, then full_name+position)
    so a single real player comes back as one row carrying both id systems,
    instead of two fragmented half-rows -- Sleeper's gsis_id is NULL for most
    rows, so a direct-only join silently splits players like Justin Jefferson
    in half. The `matched_via` field on each row says which rung of the chain
    was used (`gsis`, `espn`, `name_position`, `sleeper_only`, or
    `nflverse_only`) -- treat `name_position` as a heuristic match, not a
    certainty. Returns a candidate list (capped) if multiple distinct players
    match, or a helpful message if none do.
    """
    name_param = name.strip()
    if not name_param:
        return "Error: please provide a player name."
    if len(name_param) > MAX_NAME_LENGTH:
        return f"Error: name must be at most {MAX_NAME_LENGTH} characters."

    safe_name = name_param.replace("'", "''")
    name_filter = f"%{safe_name}%"

    sql = _SQL.format(
        name_filter=name_filter, exact_name=safe_name, limit=CANDIDATE_LIMIT
    )

    try:
        rows = run_query(sql)
    except Exception:
        logger.exception("player_lookup query failed")
        return "Error: unable to query data."

    if not rows:
        return f"No player matching '{name_param}'. Try a shorter fragment or last name only."

    return json.dumps(rows, default=str)


def register(mcp: FastMCP) -> None:
    """Register the player lookup tool."""
    mcp.tool()(player_lookup)
