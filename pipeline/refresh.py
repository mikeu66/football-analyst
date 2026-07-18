"""Pull NFL data from nflverse into a local DuckDB database.

Usage:
    uv run python pipeline/refresh.py [--seasons 2023 2024 2025]

Re-running replaces each table wholesale, so it is safe to run any time
(nflverse updates stats within ~24h of games during the season).
"""

from __future__ import annotations

import argparse

import nflreadpy as nfl
import polars as pl

from db import DB_PATH, write_tables

CURRENT_SEASON = 2025
DEFAULT_SEASONS = [CURRENT_SEASON - 2, CURRENT_SEASON - 1, CURRENT_SEASON]


def load_tables(seasons: list[int]) -> dict[str, pl.DataFrame]:
    print(f"Fetching nflverse data for seasons {seasons} ...")
    return {
        # One row per player per week: yards, TDs, targets, fantasy points, etc.
        "player_stats": nfl.load_player_stats(seasons),
        # Player identity/bio reference (one row per player, all-time).
        "players": nfl.load_players(),
        # Offensive/defensive snap counts per player per week.
        "snap_counts": nfl.load_snap_counts(seasons),
        # Game schedules and results (includes spreads/totals).
        "schedules": nfl.load_schedules(seasons),
        # Expected fantasy points from the ffverse opportunity model.
        "ff_opportunity": nfl.load_ff_opportunity(seasons),
        # Weekly injury report designations.
        "injuries": nfl.load_injuries(seasons),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seasons", type=int, nargs="+", default=DEFAULT_SEASONS,
        help=f"Seasons to load (default: {DEFAULT_SEASONS})",
    )
    args = parser.parse_args()

    tables = load_tables(args.seasons)
    write_tables(tables)
    print(f"Done. Database at {DB_PATH}")


if __name__ == "__main__":
    main()
