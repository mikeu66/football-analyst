"""Pull PPR ADP from Fantasy Football Calculator into the local DuckDB.

Usage:
    uv run python pipeline/adp.py [--year 2026] [--teams 12]

Tables written:
    ffc_adp — one row per drafted player: ADP, draft-position stats, and a
              best-effort gsis_id bridged from the nflverse players table
              (matched on name + position, tiebroken by team then recency).

FFC aggregates real mock/live drafts from the trailing window; no API key
required. Re-run daily during draft season — the table is replaced wholesale.
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from datetime import date

import duckdb
import polars as pl
import requests

from db import DB_PATH, write_tables

API = "https://fantasyfootballcalculator.com/api/v1/adp"

# Normalize both sources to nflverse-style codes for team tiebreaks only.
TEAM_ALIASES = {"LAR": "LA", "STL": "LA", "OAK": "LV", "SD": "LAC",
                "AZ": "ARI", "WSH": "WAS", "JAC": "JAX"}

SUFFIXES = re.compile(r"\s+(jr|sr|ii|iii|iv|v)$")


def norm_name(name: str, strip_suffix: bool = False) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-z ]", "", name.lower().replace("-", " ")).strip()
    n = re.sub(r"\s+", " ", n)
    return SUFFIXES.sub("", n) if strip_suffix else n


def fetch_adp(year: int, teams: int) -> tuple[pl.DataFrame, dict]:
    print(f"Fetching FFC PPR ADP for {year} ({teams}-team) ...")
    resp = requests.get(f"{API}/ppr", params={"teams": teams, "year": year}, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") != "Success" or not payload.get("players"):
        raise RuntimeError(f"FFC returned no ADP data for {year}: {payload.get('status')}")
    meta = payload["meta"]
    df = pl.DataFrame(payload["players"]).rename({"player_id": "ffc_id"})
    return df, meta


def load_nflverse_players() -> list[dict]:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        cur = con.execute(
            "SELECT gsis_id, display_name, position, latest_team "
            "FROM players WHERE gsis_id IS NOT NULL AND position IS NOT NULL"
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        con.close()


def bridge_gsis(adp: pl.DataFrame) -> pl.DataFrame:
    """Attach gsis_id by (normalized name, position); team then gsis recency break ties.

    Two passes: suffixes kept, then suffix-stripped for leftovers. Suffixes are
    load-bearing — 'Marvin Harrison' (retired) and 'Marvin Harrison Jr.' are
    both active-status WRs in the players table.
    """
    candidates: dict[tuple[str, str], list[dict]] = {}
    by_name: dict[str, list[dict]] = {}
    for p in load_nflverse_players():
        for strip in (False, True):
            key = (norm_name(p["display_name"], strip), p["position"])
            candidates.setdefault(key, []).append(p)
        by_name.setdefault(norm_name(p["display_name"], strip_suffix=True), []).append(p)

    def match(name: str, position: str, team: str | None) -> str | None:
        position = {"PK": "K"}.get(position, position)
        if position == "DEF":
            return None
        team_n = TEAM_ALIASES.get(team or "", team)

        def same_team(r: dict) -> bool:
            return TEAM_ALIASES.get(r["latest_team"], r["latest_team"]) == team_n

        for strip in (False, True):
            rows = candidates.get((norm_name(name, strip), position), [])
            if not rows:
                continue
            pool = [r for r in rows if same_team(r)] or rows
            return max(pool, key=lambda r: r["gsis_id"])["gsis_id"]
        # Cross-position fallback (e.g. two-way players listed WR by FFC, CB
        # by nflverse): only when the name is unique AND the team agrees.
        loose = by_name.get(norm_name(name, strip_suffix=True), [])
        if len(loose) == 1 and same_team(loose[0]):
            return loose[0]["gsis_id"]
        return None

    gsis = [match(r["name"], r["position"], r["team"])
            for r in adp.iter_rows(named=True)]
    out = adp.with_columns(pl.Series("gsis_id", gsis, dtype=pl.String))
    skill = out.filter(pl.col("position") != "DEF")
    matched = skill.filter(pl.col("gsis_id").is_not_null()).height
    print(f"  gsis_id bridged for {matched}/{skill.height} skill players")
    unmatched = skill.filter(pl.col("gsis_id").is_null())
    if unmatched.height:
        names = ", ".join(unmatched.get_column("name").to_list()[:10])
        print(f"  unmatched: {names}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=date.today().year)
    ap.add_argument("--teams", type=int, default=12)
    args = ap.parse_args()

    adp, meta = fetch_adp(args.year, args.teams)
    adp = bridge_gsis(adp)
    adp = adp.with_columns(
        pl.lit(args.year).alias("year"),
        pl.lit("ppr").alias("scoring"),
        pl.lit(args.teams).alias("league_teams"),
        pl.lit(meta["total_drafts"]).alias("total_drafts"),
        pl.lit(meta["start_date"]).alias("window_start"),
        pl.lit(meta["end_date"]).alias("window_end"),
    )
    write_tables({"ffc_adp": adp})
    print(f"Done. Database at {DB_PATH}")


if __name__ == "__main__":
    main()
