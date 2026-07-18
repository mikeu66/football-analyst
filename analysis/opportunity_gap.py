"""Worked example: find buy-low candidates via the opportunity gap.

Players whose actual fantasy scoring trails their expected fantasy points
(from usage: targets, air yards, carries, field position) tend to regress
upward — the usage is real, the results lag. This ranks the current-season
laggards and joins Sleeper trending to flag which ones the market is
already chasing.

    uv run python analysis/opportunity_gap.py
"""

from __future__ import annotations

from pathlib import Path

import duckdb

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "nfl.duckdb"

SQL = """
WITH gap AS (
    SELECT
        o.player_id,
        o.full_name,
        o.position,
        o.posteam,
        SUM(o.total_fantasy_points)      AS actual,
        SUM(o.total_fantasy_points_exp)  AS expected,
        SUM(o.total_fantasy_points_diff) AS diff
    FROM ff_opportunity o
    WHERE o.season = (SELECT MAX(season) FROM ff_opportunity)
      AND o.position IN ('QB', 'RB', 'WR', 'TE')
    GROUP BY ALL
    HAVING SUM(o.total_fantasy_points_exp) > 50   -- real usage only
),
trending AS (
    SELECT sp.gsis_id, st.count AS adds_24h
    FROM sleeper_trending st
    JOIN sleeper_players sp USING (player_id)
    WHERE st.kind = 'add'
)
SELECT
    g.full_name                    AS player,
    g.position,
    g.posteam                      AS team,
    ROUND(g.actual, 1)             AS actual_pts,
    ROUND(g.expected, 1)           AS expected_pts,
    ROUND(g.diff, 1)               AS gap,
    COALESCE(t.adds_24h, 0)        AS sleeper_adds_24h
FROM gap g
LEFT JOIN trending t ON t.gsis_id = g.player_id
ORDER BY g.diff
LIMIT 20
"""


def main() -> None:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    print("Top 20 buy-low candidates (scoring below expected usage):\n")
    print(con.sql(SQL))


if __name__ == "__main__":
    main()
