"""Opportunity gap analysis: find buy-low and sell-high candidates.

Call this when the user asks who to buy low on, who's due for positive
regression, who's underperforming their usage, or who to sell high.
Negative gap = actual scoring trails expected (buy-low); positive gap =
outperforming opportunity (sell-high/regression risk).
"""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import FastMCP

from mcp_server.db import run_query

logger = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    """Register the opportunity_gap tool."""

    @mcp.tool()
    def opportunity_gap(
        position: str | None = None,
        season: int | None = None,
        last_n_weeks: int | None = None,
    ) -> str:
        """Find players underperforming or overperforming their usage.

        Parameters:
        - position: Optional position filter (QB, RB, WR, TE). If None, all
          four positions are included.
        - season: Optional season (default: latest season in data).
        - last_n_weeks: Optional; when given, restrict to the last N regular-season
          weeks (week <= 18). When None, use the whole regular season.

        Returns a JSON structure with a header stating the season and week range
        used (e.g., "Season 2025, weeks 16-18 (regular season)"), followed by a
        list of players sorted by gap ascending (most negative = biggest buy-low
        opportunity first). Columns: player, position, team, actual_pts,
        expected_pts, gap, games (played in window), actual_per_game,
        expected_per_game, gap_per_game. Floats are rounded to 1–2 decimals.
        Results capped at 25 rows.

        The usage floor (minimum expected points to filter noise) scales with
        last_n_weeks to remain meaningful on shorter windows. For full season,
        floor is 50 points; for partial season, floor is scaled proportionally
        (roughly 50 * n_weeks / 17, floored at 10).
        """
        # Validate and coerce inputs
        if position is not None:
            position = position.upper()
            if position not in {"QB", "RB", "WR", "TE"}:
                return f"Error: position must be one of QB, RB, WR, TE, got '{position}'."

        if season is not None:
            try:
                season = int(season)
            except (ValueError, TypeError):
                return f"Error: season must be an integer, got '{season}'."

        if last_n_weeks is not None:
            try:
                last_n_weeks = int(last_n_weeks)
                if last_n_weeks <= 0:
                    return f"Error: last_n_weeks must be positive, got {last_n_weeks}."
            except (ValueError, TypeError):
                return f"Error: last_n_weeks must be an integer, got '{last_n_weeks}'."

        # Determine the season to use
        if season is not None:
            current_season = season
        else:
            try:
                season_result = run_query("SELECT MAX(season) FROM ff_opportunity")
                if not season_result:
                    return "Error: no data available in ff_opportunity."
                current_season = int(season_result[0]["max(season)"])
            except Exception:
                logger.exception("opportunity_gap failed querying max season")
                return "Error: unable to determine the current season."

        # Determine max regular season week for this season
        try:
            max_reg_result = run_query(
                f"SELECT MAX(week) as max_week FROM ff_opportunity "
                f"WHERE season = '{current_season}' AND week <= 18"
            )
            if not max_reg_result or max_reg_result[0]["max_week"] is None:
                return f"Error: no regular season data found for season {current_season}."
            max_reg_week = int(max_reg_result[0]["max_week"])
        except Exception:
            logger.exception(
                "opportunity_gap failed querying max regular season week for season=%s",
                current_season,
            )
            return "Error: unable to determine the regular season week range."

        # Calculate week range for header
        if last_n_weeks is not None:
            min_week = max_reg_week - last_n_weeks + 1
            max_week = max_reg_week
        else:
            min_week = 1
            max_week = max_reg_week

        # Format header
        header = f"Season {current_season}, weeks {min_week}-{max_week} (regular season)"

        # Build position filter
        if position is not None:
            position_filter = f"AND o.position = '{position}'"
        else:
            position_filter = "AND o.position IN ('QB', 'RB', 'WR', 'TE')"

        # Build usage floor and week filter
        # Always exclude playoff weeks (>18) from aggregation
        if last_n_weeks is not None:
            usage_floor = max(10, int(50 * last_n_weeks / 17))
            having_clause = f"HAVING SUM(o.total_fantasy_points_exp) > {usage_floor}"
            week_filter = f"AND o.week > {max_reg_week - last_n_weeks} AND o.week <= 18"
        else:
            having_clause = "HAVING SUM(o.total_fantasy_points_exp) > 50"
            week_filter = "AND o.week <= 18"

        sql = f"""
        WITH gap AS (
            SELECT
                o.player_id,
                o.full_name,
                o.position,
                o.posteam,
                COUNT(*) AS games,
                SUM(o.total_fantasy_points)      AS actual,
                SUM(o.total_fantasy_points_exp)  AS expected,
                SUM(o.total_fantasy_points_diff) AS diff
            FROM ff_opportunity o
            WHERE o.season = '{current_season}'
              {position_filter}
              {week_filter}
            GROUP BY o.player_id, o.full_name, o.position, o.posteam
            {having_clause}
        )
        SELECT
            g.full_name                                   AS player,
            g.position,
            g.posteam                                     AS team,
            ROUND(g.actual, 1)                            AS actual_pts,
            ROUND(g.expected, 1)                          AS expected_pts,
            ROUND(g.diff, 1)                              AS gap,
            g.games,
            ROUND(g.actual / g.games, 2)                  AS actual_per_game,
            ROUND(g.expected / g.games, 2)                AS expected_per_game,
            ROUND(g.diff / g.games, 2)                    AS gap_per_game
        FROM gap g
        ORDER BY g.diff ASC
        LIMIT 25
        """

        try:
            rows = run_query(sql)
        except Exception:
            logger.exception("opportunity_gap query failed")
            return "Error: unable to query data."

        if not rows:
            return json.dumps(
                {"header": header, "data": [], "message": "No players found matching the criteria."},
                default=str
            )

        result_data = {
            "header": header,
            "data": rows
        }

        return json.dumps(result_data, default=str)
