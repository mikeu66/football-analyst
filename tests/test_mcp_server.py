"""Tests for the NFL DuckDB MCP server (T7).

Runs against the real `data/nfl.duckdb` file (deliberate per
docs/mcp-server-plan.md — it's a local read-only file, not a fixture).

`query`, `data_status`, `describe_data` are importable directly from
`mcp_server.server`. `opportunity_gap`, `trending`, `injury_report` are
defined inside `register(mcp)` in tools_opportunity.py / tools_market.py and
are not importable at module scope, so we capture the underlying function
objects with a tiny shim object that mimics FastMCP's `.tool()` decorator
(the decorator returns the function unchanged, so the shim captures the
exact same callables that would be registered on a real FastMCP instance).
"""

from __future__ import annotations

import ast
import json

import pytest

from mcp_server.server import data_status, describe_data, query
from mcp_server.tools_players import player_lookup
from mcp_server import tools_opportunity, tools_market


class _ToolShim:
    """Stand-in for FastMCP: captures functions passed to .tool()."""

    def __init__(self) -> None:
        self.captured: dict[str, object] = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.captured[fn.__name__] = fn
            return fn

        return deco


def _capture_tools() -> dict[str, object]:
    shim = _ToolShim()
    tools_opportunity.register(shim)
    tools_market.register(shim)
    return shim.captured


_TOOLS = _capture_tools()
opportunity_gap = _TOOLS["opportunity_gap"]
trending = _TOOLS["trending"]
injury_report = _TOOLS["injury_report"]


# --- 1. `query` guardrails -----------------------------------------------


def test_query_rejects_insert():
    result = query("INSERT INTO players VALUES (1)")
    assert isinstance(result, str)
    assert "Error" in result
    assert "INSERT" in result


def test_query_rejects_update():
    result = query("UPDATE players SET status = 'X'")
    assert isinstance(result, str)
    assert "Error" in result
    assert "UPDATE" in result


def test_query_rejects_drop():
    result = query("DROP TABLE players")
    assert isinstance(result, str)
    assert "Error" in result
    assert "DROP" in result


def test_query_rejects_multi_statement():
    result = query("SELECT 1; SELECT 2")
    assert isinstance(result, str)
    assert "Error" in result
    assert "single SQL statement" in result


def test_query_allows_trailing_semicolon():
    result = query("SELECT 1 AS x;")
    assert isinstance(result, str)
    assert "Error" not in result
    rows = ast.literal_eval(result)
    assert rows == [{"x": 1}]


def test_query_allows_semicolon_inside_string_literal():
    result = query("SELECT 'a;b' AS x")
    assert isinstance(result, str)
    assert "Error" not in result
    rows = ast.literal_eval(result)
    assert rows == [{"x": "a;b"}]


def test_query_appends_row_cap_when_no_limit():
    result = query("SELECT * FROM player_stats")
    rows = ast.literal_eval(result)
    assert len(rows) == 200


def test_query_respects_explicit_limit():
    result = query("SELECT * FROM player_stats LIMIT 3")
    rows = ast.literal_eval(result)
    assert len(rows) == 3


def test_query_bad_column_returns_error_not_exception():
    # Must not raise; must come back as a helpful error string.
    result = query("SELECT this_column_does_not_exist FROM player_stats")
    assert isinstance(result, str)
    assert "Error" in result


# --- 2. `data_status` -----------------------------------------------------


def test_data_status_mentions_all_tables_and_max_season():
    result = data_status()
    assert isinstance(result, str)
    for table in [
        "player_stats",
        "ff_opportunity",
        "players",
        "snap_counts",
        "injuries",
        "schedules",
        "sleeper_players",
        "sleeper_trending",
    ]:
        assert table in result

    # Every table that carries season/week should report max_season=2025.
    for line in result.splitlines():
        if "max_season" in line:
            assert "max_season=2025" in line


# --- 3. `describe_data` ----------------------------------------------------


def test_describe_data_returns_semantics_doc():
    result = describe_data()
    assert isinstance(result, str)
    assert len(result) > 0
    assert not result.startswith("Error")
    # Sanity: this is the semantics doc, not some other text.
    assert "gsis" in result.lower()


# --- 4a. player_lookup happy paths + regression tests ----------------------


def test_player_lookup_mahomes():
    result = player_lookup("Patrick Mahomes")
    rows = json.loads(result)
    assert len(rows) == 1
    row = rows[0]
    assert row["gsis_id"] == "00-0033873"
    assert row["matched_via"] == "gsis"


def test_player_lookup_chase_name_position_bridge():
    """Regression test: Ja'Marr Chase has NULL gsis_id AND NULL espn_id on
    the Sleeper side, so he can only be bridged via (full_name, position).
    A naive gsis-only join fragments him into two half-rows; this must
    come back as exactly one row.
    """
    result = player_lookup("Ja'Marr Chase")
    rows = json.loads(result)
    assert len(rows) == 1
    row = rows[0]
    assert row["sleeper_player_id"] == "7564"
    assert row["gsis_id"] == "00-0036900"
    assert row["matched_via"] == "name_position"


def test_player_lookup_jefferson_disambiguates_two_humans():
    """Regression test: there are two distinct 'Justin Jefferson's --
    the MIN WR (fantasy-relevant, bridged via espn_id, gsis 00-0036322,
    Sleeper 6794) and a CLE LB. They must NOT be merged into one row.
    """
    result = player_lookup("Justin Jefferson")
    rows = json.loads(result)

    wr_rows = [r for r in rows if r["position"] == "WR"]
    lb_rows = [r for r in rows if r["position"] == "LB"]

    assert len(wr_rows) == 1
    assert wr_rows[0]["gsis_id"] == "00-0036322"
    assert wr_rows[0]["sleeper_player_id"] == "6794"

    assert len(lb_rows) == 1
    assert lb_rows[0]["team"] == "CLE"
    # The two humans must be separate rows, not merged.
    assert wr_rows[0]["gsis_id"] != lb_rows[0].get("gsis_id")


# --- 4b. opportunity_gap ----------------------------------------------------


def test_opportunity_gap_default_shape_and_playoff_exclusion():
    result = opportunity_gap()
    parsed = json.loads(result)
    assert "header" in parsed
    assert "data" in parsed
    assert len(parsed["data"]) > 0

    gaps = [row["gap"] for row in parsed["data"]]
    assert gaps == sorted(gaps)  # sorted ascending by gap

    # Regression test: playoff weeks 19-22 must be excluded (they only
    # have 2-8 teams), so no player can have more than 18 games (a full
    # regular season).
    for row in parsed["data"]:
        assert row["games"] <= 18


def test_opportunity_gap_position_and_window_filter():
    result = opportunity_gap(position="WR", last_n_weeks=3)
    parsed = json.loads(result)
    assert "weeks 16-18" in parsed["header"]
    assert len(parsed["data"]) > 0
    for row in parsed["data"]:
        assert row["position"] == "WR"
        assert row["games"] <= 3


def test_opportunity_gap_bad_position_helpful_message():
    result = opportunity_gap(position="ZZ")
    assert isinstance(result, str)
    assert "Error" in result
    assert "ZZ" in result


# --- 4c. trending -----------------------------------------------------------


def test_trending_add_returns_real_names():
    result = trending("add")
    assert isinstance(result, str)
    lines = [
        line.strip()
        for line in result.splitlines()
        if line.strip() and not line.startswith("Top trending")
    ]
    assert len(lines) > 0
    # Each line must contain at least one alphabetic "word" (a real name),
    # not just be a bare numeric Sleeper player_id.
    for line in lines:
        assert any(part.isalpha() for part in line.replace(",", " ").split())


def test_trending_banana_helpful_message():
    result = trending("banana")
    assert isinstance(result, str)
    assert "banana" in result
    assert "add" in result and "drop" in result


# --- 4d. injury_report -------------------------------------------------------


def test_injury_report_kc_row_count_matches_header():
    """Regression test: this previously returned 546 rows spanning
    2023-2025 while the header claimed week 18. The header and body
    must agree, and KC's week-18-2025 report is exactly 11 rows.
    """
    result = injury_report(team="KC")
    assert isinstance(result, str)
    assert "Season: 2025, Week: 18" in result

    # Body rows have "  Report: " (two spaces) before the status; the
    # "NFL Injury Report:" title line has only one, so this excludes it.
    body_rows = [line for line in result.splitlines() if "  Report:" in line]
    assert len(body_rows) == 11
    for line in body_rows:
        assert "  KC  " in line


def test_injury_report_lar_matches_la():
    """Regression test: Sleeper calls the Rams 'LAR', `injuries` calls
    them 'LA'. 'LAR' used to be rejected outright.
    """
    result_lar = injury_report(team="LAR")
    result_la = injury_report(team="LA")
    assert isinstance(result_lar, str)
    assert "Error" not in result_lar
    assert "Invalid" not in result_lar
    assert result_lar == result_la


def test_injury_report_bad_team_helpful_message():
    result = injury_report(team="ZZZ")
    assert isinstance(result, str)
    assert "Invalid" in result
    assert "ZZZ" in result


# --- 5. No tool raises on garbage input --------------------------------------


GARBAGE_INPUTS = ["", " ", "!!!", "🏈" * 5, "'; DROP TABLE players; --", None]


@pytest.mark.parametrize("garbage", GARBAGE_INPUTS)
def test_query_never_raises(garbage):
    if garbage is None:
        return  # query(sql) requires a str; skip the None case for this tool.
    result = query(garbage)
    assert isinstance(result, str)


@pytest.mark.parametrize("garbage", GARBAGE_INPUTS)
def test_player_lookup_never_raises(garbage):
    if garbage is None:
        return
    result = player_lookup(garbage)
    assert isinstance(result, str)


@pytest.mark.parametrize("garbage", ["", " ", "!!!", "🏈" * 5, "'; DROP TABLE players; --", "ZZZZZ", None])
def test_opportunity_gap_never_raises(garbage):
    result = opportunity_gap(position=garbage)
    assert isinstance(result, str)


@pytest.mark.parametrize("garbage", ["", " ", "!!!", "🏈" * 5, "'; DROP TABLE players; --", None])
def test_trending_never_raises(garbage):
    if garbage is None:
        result = trending()
    else:
        result = trending(garbage)
    assert isinstance(result, str)


@pytest.mark.parametrize("garbage", ["", " ", "!!!", "🏈" * 5, "'; DROP TABLE players; --", "ZZZZZ"])
def test_injury_report_never_raises(garbage):
    result = injury_report(team=garbage)
    assert isinstance(result, str)
