"""Draft prep: current ADP vs. last season's expected-points production.

Call this when the user asks about draft values, ADP, who to target or
avoid in drafts, or whether a player is being drafted too high or too low.
Positive value_score = drafted later than last season's usage-based
positional rank (potential steal); negative = drafted earlier (reach).
"""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import FastMCP

from mcp_server.db import run_query

logger = logging.getLogger(__name__)

MIN_GAMES = 4


def register(mcp: FastMCP) -> None:
    """Register the adp_value tool."""

    @mcp.tool()
    def adp_value(
        player: str | None = None,
        position: str | None = None,
        sort: str = "value",
        limit: int = 25,
    ) -> str:
        """Compare current draft ADP against last season's expected fantasy points.

        Call this for draft-prep questions: "who are the best values in
        drafts right now", "who's being over-drafted", "show me the ADP
        board", "is Player X worth their draft cost". Data is PPR ADP from
        Fantasy Football Calculator (rolling window of real drafts), joined
        to last season's ffverse expected-points model.

        Parameters:
        - player: Case-insensitive substring match on player name. Use this
          for single-player questions ("is Breece Hall worth his ADP?") —
          it returns that player's row(s) with ranks computed against the
          full board, ignoring sort. No match means the player carries no
          FFC ADP, i.e. they are going undrafted in recent mock drafts —
          that itself answers the cost question (late-round flier).
        - position: Optional filter (QB, RB, WR, TE). Kickers and team
          defenses carry ADP but no expected-points model, so they only
          appear in sort="adp" or player searches.
        - sort: "value" (default) = biggest positive value_score first
          (drafted later than last season's usage rank — potential steals);
          "reach" = most negative first (drafted well ahead of last
          season's production — potential fades); "adp" = draft-board
          order, including rookies and others with no prior-season data.
        - limit: Max rows (default 25, capped at 50).

        Output is JSON: a header stating the ADP snapshot (year, league
        size, draft count, date window) and prior season used, then rows
        with: player, position, team, bye, adp, adp_formatted (round.pick),
        adp_pos_rank, prior-season games / expected_per_game /
        actual_per_game, exp_pos_rank (rank by expected points per game,
        min 4 games), and value_score = adp_pos_rank − exp_pos_rank.
        Rookies have no prior-season data: value_score is NULL and they are
        excluded from "value"/"reach" sorts — a rookie's absence there is
        not a judgment about the pick.
        """
        if player is not None:
            player = player.strip()
            if not player or len(player) > 100:
                return "Error: player must be a non-empty name under 100 characters."

        if position is not None:
            position = position.upper()
            if position not in {"QB", "RB", "WR", "TE"}:
                return f"Error: position must be one of QB, RB, WR, TE, got '{position}'."

        if sort not in {"value", "reach", "adp"}:
            return f"Error: sort must be one of value, reach, adp, got '{sort}'."

        try:
            limit = max(1, min(int(limit), 50))
        except (ValueError, TypeError):
            return f"Error: limit must be an integer, got '{limit}'."

        try:
            meta_rows = run_query(
                "SELECT year, league_teams, total_drafts, window_start, window_end "
                "FROM ffc_adp LIMIT 1"
            )
        except Exception:
            logger.exception("adp_value failed reading ffc_adp meta")
            return (
                "Error: no ADP data loaded. Run `uv run python pipeline/adp.py` "
                "to fetch it."
            )
        if not meta_rows:
            return "Error: ffc_adp table is empty. Run `uv run python pipeline/adp.py`."
        meta = meta_rows[0]
        prior_season = int(meta["year"]) - 1

        if position is not None:
            position_filter = f"AND a.position = '{position}'"
        elif player is not None or sort == "adp":
            position_filter = ""
        else:
            position_filter = "AND a.position IN ('QB', 'RB', 'WR', 'TE')"

        if player is not None:
            player_esc = player.replace("'", "''")
            row_filter = f"WHERE player ILIKE '%{player_esc}%'"
            order = "adp ASC"
        else:
            order = {
                "value": "value_score DESC NULLS LAST, adp ASC",
                "reach": "value_score ASC NULLS LAST, adp ASC",
                "adp": "adp ASC",
            }[sort]
            row_filter = "WHERE value_score IS NOT NULL" if sort != "adp" else ""

        sql = f"""
        WITH prior AS (
            SELECT
                player_id,
                COUNT(*) AS games,
                SUM(total_fantasy_points)     AS actual,
                SUM(total_fantasy_points_exp) AS expected
            FROM ff_opportunity
            WHERE season = '{prior_season}' AND week <= 18
            GROUP BY player_id
        ),
        board AS (
            SELECT
                a.name AS player,
                a.position,
                a.team,
                a.bye,
                a.adp,
                a.adp_formatted,
                p.games,
                ROUND(p.expected / p.games, 2) AS expected_per_game,
                ROUND(p.actual / p.games, 2)   AS actual_per_game,
                ROW_NUMBER() OVER (PARTITION BY a.position ORDER BY a.adp) AS adp_pos_rank,
                CASE WHEN p.games >= {MIN_GAMES} THEN
                    ROW_NUMBER() OVER (
                        PARTITION BY a.position, (p.games >= {MIN_GAMES})
                        ORDER BY p.expected / p.games DESC
                    )
                END AS exp_pos_rank
            FROM ffc_adp a
            LEFT JOIN prior p ON p.player_id = a.gsis_id
            WHERE 1 = 1 {position_filter}
        )
        SELECT *, adp_pos_rank - exp_pos_rank AS value_score
        FROM board
        {row_filter}
        ORDER BY {order}
        LIMIT {limit}
        """

        try:
            rows = run_query(sql)
        except Exception:
            logger.exception("adp_value query failed")
            return "Error: unable to query ADP data."

        header = (
            f"FFC PPR ADP, {meta['year']} season, {meta['league_teams']}-team leagues, "
            f"{meta['total_drafts']} drafts from {meta['window_start']} to "
            f"{meta['window_end']}; production baseline: {prior_season} regular season "
            f"(expected points per game, min {MIN_GAMES} games)"
        )

        if not rows:
            if player is not None:
                message = (
                    f"No player matching '{player}' in the current ADP data. "
                    "Only players drafted in recent FFC mocks carry an ADP "
                    "(~200 players); anyone outside that set is going "
                    "undrafted — effectively a late-round flier. If this is "
                    "unexpected, verify the spelling with player_lookup."
                )
            else:
                message = "No players found matching the criteria."
            return json.dumps(
                {"header": header, "data": [], "message": message},
                default=str,
            )

        return json.dumps({"header": header, "data": rows}, default=str)
