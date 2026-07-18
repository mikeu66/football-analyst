"""Read-only DuckDB access for the MCP server.

The pipeline replaces tables wholesale on refresh, so a long-lived connection
can see a half-swapped database. Open a fresh connection per query instead.
"""

from __future__ import annotations

import threading
from pathlib import Path

import duckdb

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "nfl.duckdb"

# Table functions like read_csv()/glob()/httpfs URLs ignore read_only=True
# (that flag only protects the attached file); this config actually disables
# local file and network access from within SQL.
CONNECT_CONFIG = {"enable_external_access": False}

QUERY_TIMEOUT_SECONDS = 10
MEMORY_LIMIT = "512MB"


def run_query(sql: str) -> list[dict]:
    """Run a SQL statement against the NFL DuckDB and return rows as dicts."""
    con = duckdb.connect(str(DB_PATH), read_only=True, config=CONNECT_CONFIG)
    con.execute(f"SET memory_limit='{MEMORY_LIMIT}'")
    timer = threading.Timer(QUERY_TIMEOUT_SECONDS, con.interrupt)
    timer.start()
    try:
        result = con.execute(sql)
        cols = [d[0] for d in result.description]
        return [dict(zip(cols, row)) for row in result.fetchall()]
    finally:
        timer.cancel()
        con.close()
