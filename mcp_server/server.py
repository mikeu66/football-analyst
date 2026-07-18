"""FastMCP stdio server over the NFL DuckDB dataset.

Run with: uv run python -m mcp_server.server
"""

from __future__ import annotations

import datetime
import logging
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server.db import DB_PATH, run_query

logger = logging.getLogger(__name__)

mcp = FastMCP("nfl-data")

SEMANTICS_PATH = Path(__file__).resolve().parent / "semantics.md"

ALLOWED_FIRST_KEYWORDS = {"SELECT", "WITH", "DESCRIBE", "SHOW", "SUMMARIZE"}

ROW_CAP = 200

STATUS_TABLES = [
    "player_stats",
    "ff_opportunity",
    "players",
    "snap_counts",
    "injuries",
    "schedules",
    "sleeper_players",
    "sleeper_trending",
]

TABLES_WITH_SEASON_WEEK = {
    "player_stats",
    "ff_opportunity",
    "snap_counts",
    "injuries",
    "schedules",
}


def _strip_trailing_semicolon(sql: str) -> str:
    stripped = sql.strip()
    if stripped.endswith(";"):
        stripped = stripped[:-1].strip()
    return stripped


def _has_stray_semicolon(sql: str) -> bool:
    """True if a semicolon remains outside of quoted string literals."""
    in_single = False
    in_double = False
    for ch in sql:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ";" and not in_single and not in_double:
            return True
    return False


def _first_keyword(sql: str) -> str:
    match = re.match(r"\s*([A-Za-z]+)", sql)
    return match.group(1).upper() if match else ""


def _has_limit(sql: str) -> bool:
    return re.search(r"\blimit\b", sql, re.IGNORECASE) is not None


@mcp.tool()
def query(sql: str) -> str:
    """Run a read-only SQL query against the NFL DuckDB dataset.

    Call this when the curated tools (player_lookup, opportunity_gap,
    trending, injury_report) don't cover what's being asked and you need
    direct access to the underlying tables (player_stats, ff_opportunity,
    players, snap_counts, injuries, schedules, sleeper_players,
    sleeper_trending). Only SELECT / WITH / DESCRIBE / SHOW / SUMMARIZE
    statements are allowed, one statement per call. Results are capped at
    200 rows unless the query supplies its own LIMIT. Returns the rows as
    text, or an explanatory error message if the query is rejected or fails.
    """
    body = _strip_trailing_semicolon(sql)

    if not body:
        return "Error: empty query."

    if _has_stray_semicolon(body):
        return "Error: only a single SQL statement is allowed per call."

    keyword = _first_keyword(body)
    if keyword not in ALLOWED_FIRST_KEYWORDS:
        return (
            f"Error: query must start with one of {sorted(ALLOWED_FIRST_KEYWORDS)}, "
            f"got '{keyword}'. This tool is read-only."
        )

    if keyword in {"SELECT", "WITH"} and not _has_limit(body):
        body = f"{body}\nLIMIT {ROW_CAP}"

    try:
        rows = run_query(body)
    except Exception:  # noqa: BLE001 - surface as text, never raise
        logger.exception("query tool failed for sql=%r", body)
        return "Error: unable to run that query."

    if not rows:
        return "Query returned no rows."

    return str(rows)


@mcp.tool()
def describe_data() -> str:
    """Explain what data is available and how to query it correctly.

    Call this FIRST, before writing any SQL with the `query` tool, and any
    time a question needs a column the curated tools don't expose. The two
    main tables are too wide to inspect by hand (player_stats has 145
    columns, ff_opportunity 159); this returns a curated map of the columns
    that matter, the join keys, scoring conventions, and the gotchas that
    otherwise produce silently wrong answers.
    """
    try:
        return SEMANTICS_PATH.read_text()
    except OSError as exc:
        return f"Error reading semantics doc: {exc}"


@mcp.tool()
def data_status() -> str:
    """Report freshness and size of the NFL DuckDB dataset.

    Call this when asked how current the data is, what seasons/weeks are
    loaded, or how large each table is (e.g. "is this up to date?",
    "what's the latest week of data?"). Returns per-table row counts, the
    max season/week for tables that carry those columns, and the DuckDB
    file's last-modified time as a proxy for the last refresh.
    """
    lines = []
    try:
        mtime = DB_PATH.stat().st_mtime
    except OSError:
        logger.exception("data_status failed to stat DB file")
        return "Error: unable to read the database file."

    lines.append(
        f"DB file last modified: {datetime.datetime.fromtimestamp(mtime).isoformat()}"
    )

    for table in STATUS_TABLES:
        try:
            count_rows = run_query(f"SELECT COUNT(*) AS n FROM {table}")
            count = count_rows[0]["n"]
        except Exception:  # noqa: BLE001
            logger.exception("data_status failed counting table=%s", table)
            lines.append(f"{table}: error retrieving status")
            continue

        if table in TABLES_WITH_SEASON_WEEK:
            span_rows = run_query(
                f"SELECT MAX(season) AS max_season, MAX(week) AS max_week FROM {table}"
            )
            max_season = span_rows[0]["max_season"]
            max_week = span_rows[0]["max_week"]
            lines.append(
                f"{table}: {count:,} rows, max_season={max_season}, max_week={max_week}"
            )
        else:
            lines.append(f"{table}: {count:,} rows")

    return "\n".join(lines)


# --- Curated tool registration -----------------------------------------
# Imported eagerly: a broken tool module must fail loudly at startup rather
# than leave the server running with a tool silently missing.
from mcp_server.tools_market import register as _register_market
from mcp_server.tools_opportunity import register as _register_opportunity
from mcp_server.tools_players import register as _register_players

_register_players(mcp)
_register_opportunity(mcp)
_register_market(mcp)


if __name__ == "__main__":
    mcp.run(transport="stdio")
