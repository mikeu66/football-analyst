"""Shared DuckDB helpers for the data pipeline."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "nfl.duckdb"


def write_tables(tables: dict[str, pl.DataFrame]) -> None:
    """Replace each named table in the local DuckDB with the given frame."""
    DB_PATH.parent.mkdir(exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    try:
        for name, df in tables.items():
            con.register("arrow_table", df.to_arrow())
            con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM arrow_table")
            con.unregister("arrow_table")
            print(f"  {name}: {df.height:,} rows, {df.width} cols")
    finally:
        con.close()
