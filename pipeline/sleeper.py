"""Pull fantasy-side data from the Sleeper API into the local DuckDB.

Usage:
    uv run python pipeline/sleeper.py

Tables written:
    sleeper_players   — player metadata (IDs, team, position, injury status),
                        including gsis_id for joining to nflverse tables
    sleeper_trending  — most added/dropped players over the last 24h

Sleeper asks that the full players dump be fetched at most once per day.
No API key required.
"""

from __future__ import annotations

import polars as pl
import requests

from db import DB_PATH, write_tables

BASE = "https://api.sleeper.app/v1"

# Positions worth keeping from the ~11k-player dump (skips long snappers etc.
# only in the sense of fantasy relevance; defensive players stay for the
# real-football side).
KEEP_COLS = [
    "player_id", "full_name", "first_name", "last_name", "position",
    "fantasy_positions", "team", "age", "years_exp", "status",
    "injury_status", "injury_body_part", "number", "depth_chart_position",
    "depth_chart_order", "gsis_id", "espn_id", "yahoo_id",
]


def fetch_players() -> pl.DataFrame:
    print("Fetching Sleeper player dump (~5 MB) ...")
    resp = requests.get(f"{BASE}/players/nfl", timeout=120)
    resp.raise_for_status()
    players = resp.json()  # dict keyed by sleeper player_id

    rows = []
    for pid, p in players.items():
        row = {col: p.get(col) for col in KEEP_COLS}
        row["player_id"] = pid
        # Sleeper's gsis_id values sometimes carry leading whitespace.
        if row.get("gsis_id"):
            row["gsis_id"] = str(row["gsis_id"]).strip()
        # Lists don't fit a flat table; join to a comma string.
        if row.get("fantasy_positions"):
            row["fantasy_positions"] = ",".join(row["fantasy_positions"])
        rows.append(row)
    return pl.DataFrame(rows, schema_overrides={"age": pl.Float64, "number": pl.Int64})


def fetch_trending() -> pl.DataFrame:
    frames = []
    for kind in ("add", "drop"):
        print(f"Fetching trending {kind}s ...")
        resp = requests.get(
            f"{BASE}/players/nfl/trending/{kind}",
            params={"lookback_hours": 24, "limit": 100},
            timeout=30,
        )
        resp.raise_for_status()
        frames.append(
            pl.DataFrame(resp.json()).with_columns(pl.lit(kind).alias("kind"))
        )
    return pl.concat(frames)


def main() -> None:
    write_tables({
        "sleeper_players": fetch_players(),
        "sleeper_trending": fetch_trending(),
    })
    print(f"Done. Database at {DB_PATH}")


if __name__ == "__main__":
    main()
