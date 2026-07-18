"""Market data tools: trending players and injury reports."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from mcp_server.db import run_query


def register(mcp: FastMCP) -> None:
    """Register market data tools with the MCP server."""

    @mcp.tool()
    def trending(kind: str = "add") -> str:
        """Return trending players on Sleeper (adds or drops).

        Call this when the user asks who to add off waivers, who's being
        dropped, or what the market is doing. Returns the top ~25 players
        by add/drop count (as of last refresh), with their current team
        and position.
        """
        kind_lower = kind.lower().strip()
        if kind_lower not in {"add", "drop"}:
            return (
                f"Invalid kind: '{kind}'. Must be 'add' or 'drop'. "
                "Use 'add' to see who managers are adding, 'drop' to see "
                "who's being dropped."
            )

        sql = f"""
        SELECT sp.full_name, sp.team, sp.position, st.count
        FROM sleeper_trending st
        JOIN sleeper_players sp ON sp.player_id = st.player_id
        WHERE st.kind = '{kind_lower}'
        ORDER BY st.count DESC
        LIMIT 25
        """

        rows = run_query(sql)
        if not rows:
            return f"No trending {kind_lower} data available."

        lines = [f"Top trending {kind_lower}s (as of last refresh):"]
        lines.append("")
        for row in rows:
            count = row["count"]
            name = row["full_name"]
            team = row["team"]
            pos = row["position"]
            lines.append(f"  {count:3d}  {name:20s}  {pos}  {team}")

        return "\n".join(lines)

    @mcp.tool()
    def injury_report(team: str | None = None, week: int | None = None) -> str:
        """Return injury reports for NFL players.

        Call this when the user asks who's hurt, who's questionable,
        or about a team's injury situation. By default returns the latest
        week's report (most recent season/week combination). Optional
        `team` filter (e.g. 'KC') and `week` filter. Note: NULL report_status
        means no report was filed that week, not that the player was healthy.
        """
        filters = []
        params_str = ""

        if week is not None:
            try:
                week_int = int(week)
                filters.append(f"i.week = {week_int}")
                params_str += f" week={week_int},"
            except (ValueError, TypeError):
                return f"Invalid week: '{week}'. Must be an integer."

        if team is not None:
            team_upper = _normalize_team_code(team.upper().strip())
            if team_upper is None:
                return (
                    f"Invalid team: '{team}'. Must be a 2-3 letter NFL team code "
                    "(e.g. 'KC', 'NYG', 'LV')."
                )
            filters.append(f"i.team = '{team_upper}'")
            params_str += f" team={team_upper},"

        # If week is not specified, compute the latest season/week scoped to the filters
        if week is None:
            # Build where clause for finding max season/week
            base_where = " AND ".join(filters) if filters else ""
            if base_where:
                base_where = f"WHERE {base_where}"

            # Find the max season given the filters
            latest_sql = f"""
            SELECT MAX(season) as max_season FROM injuries i {base_where}
            """
            latest_rows = run_query(latest_sql)
            if latest_rows and latest_rows[0]["max_season"] is not None:
                max_season = latest_rows[0]["max_season"]
                # Find the max week for that season, still respecting the filters
                week_sql = f"""
                SELECT MAX(week) as max_week FROM injuries i
                WHERE i.season = {max_season}
                {' AND ' + (' AND '.join(filters)) if filters else ''}
                """
                week_rows = run_query(week_sql)
                if week_rows and week_rows[0]["max_week"] is not None:
                    max_week = week_rows[0]["max_week"]
                    filters.append(f"i.season = {max_season}")
                    filters.append(f"i.week = {max_week}")

        where_clause = " AND ".join(filters) if filters else ""

        if where_clause:
            where_clause = f"WHERE {where_clause}"

        sql = f"""
        SELECT i.full_name, i.position, i.team, i.week, i.season,
               i.report_status,
               i.report_primary_injury,
               NULLIF(TRIM(i.practice_status, E' \t\r\n'), '') AS practice_status
        FROM injuries i
        {where_clause}
        ORDER BY i.season DESC, i.week DESC, i.team, i.full_name
        LIMIT 100
        """

        rows = run_query(sql)
        if not rows:
            suffix = f" for {params_str.rstrip(',')}" if params_str else ""
            return f"No injury reports found{suffix}."

        lines = ["NFL Injury Report:"]
        lines.append("")

        if rows:
            season = rows[0].get("season")
            week_val = rows[0].get("week")
            lines.append(f"Season: {season}, Week: {week_val}")
            lines.append("")
            # Check if results were truncated
            if len(rows) >= 100:
                lines.append("(Results truncated to 100 rows)")
                lines.append("")

        for row in rows:
            name = row["full_name"]
            pos = row["position"]
            team = row["team"]
            report_status = row["report_status"] or "—"
            primary_inj = row["report_primary_injury"] or "—"
            practice_status = row["practice_status"] or "—"

            lines.append(
                f"{name:20s}  {pos}  {team}  "
                f"Report: {report_status:12s}  {primary_inj:30s}  "
                f"Practice: {practice_status}"
            )

        return "\n".join(lines)


VALID_TEAMS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
    "DET", "GB", "HOU", "IND", "JAX", "KC", "LA", "LAC", "LV", "MIA",
    "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB",
    "TEN", "WAS",
}

# The sources disagree on team codes. `injuries` (and every other nflverse
# table) uses LA and ARI; Sleeper uses LAR and still carries legacy OAK;
# `players.latest_team` additionally contains AZ. Since `player_lookup`
# surfaces Sleeper's code, an LLM chaining player_lookup -> injury_report
# passes LAR and would otherwise be told LAR is not a team.
TEAM_ALIASES = {
    "LAR": "LA",    # Sleeper's Rams code
    "STL": "LA",    # pre-2016 Rams
    "OAK": "LV",    # Sleeper still lists legacy Raiders rows
    "SD": "LAC",    # pre-2017 Chargers
    "AZ": "ARI",    # players.latest_team variant
    "ARZ": "ARI",
    "WSH": "WAS",
    "JAC": "JAX",
}


def _normalize_team_code(code: str) -> str | None:
    """Map a user/Sleeper team code onto the code `injuries` actually uses."""
    canonical = TEAM_ALIASES.get(code, code)
    return canonical if canonical in VALID_TEAMS else None
