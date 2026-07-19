"""NFL player explorer — fantasy + real-football views over data/nfl.duckdb.

Run with:
    uv run streamlit run app/Home.py
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import plotly.graph_objects as go
import polars as pl
import streamlit as st

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "nfl.duckdb"

# Categorical palette, fixed slot order (colorblind-validated as a sequence —
# do not shuffle or cycle; >8 selections fold into the same hues by rank).
SERIES_COLORS = [
    "#2a78d6", "#008300", "#e87ba4", "#eda100",
    "#1baf7a", "#eb6834", "#4a3aa7", "#e34948",
]
GRID = "#e1e0d9"
MUTED = "#898781"

st.set_page_config(page_title="NFL Analysis", page_icon="🏈", layout="wide")

if not DB_PATH.exists():
    st.error(
        "No database found. Build it first:\n\n"
        "```\nuv run python pipeline/refresh.py\nuv run python pipeline/sleeper.py\n```"
    )
    st.stop()


@st.cache_resource
def connect() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DB_PATH), read_only=True)


@st.cache_data
def query(sql: str, params: list | None = None) -> pl.DataFrame:
    return connect().execute(sql, params or []).pl()


def base_layout(fig: go.Figure, *, y_title: str) -> go.Figure:
    fig.update_layout(
        template="none",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="system-ui, sans-serif", color=MUTED, size=13),
        margin=dict(l=10, r=10, t=10, b=10),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        xaxis=dict(title="Week", showgrid=False, tickmode="linear", dtick=1, automargin=True),
        yaxis=dict(title=y_title, gridcolor=GRID, zerolinecolor=GRID, automargin=True),
    )
    return fig


st.title("NFL player explorer")

seasons = query("SELECT DISTINCT season FROM player_stats ORDER BY season DESC")["season"].to_list()

with st.sidebar:
    season = st.selectbox("Season", seasons)
    positions = st.multiselect("Positions", ["QB", "RB", "WR", "TE"], default=["QB", "RB", "WR", "TE"])
    scoring = st.radio("Scoring", ["PPR", "Standard"], horizontal=True)

points_col = "fantasy_points_ppr" if scoring == "PPR" else "fantasy_points"

leaders = query(
    f"""
    SELECT player_id, player_display_name AS player, position, team,
           SUM({points_col}) AS points, COUNT(*) AS games
    FROM player_stats
    WHERE season = ? AND season_type = 'REG' AND position = ANY(?)
    GROUP BY ALL ORDER BY points DESC
    """,
    [season, positions or ["QB", "RB", "WR", "TE"]],
)

# ---------------------------------------------------------------- weekly points
st.subheader("Weekly fantasy points")
default_players = leaders["player"].head(3).to_list()
selected = st.multiselect(
    "Players (max 8)", leaders["player"].to_list(), default=default_players, max_selections=8
)

if selected:
    weekly = query(
        f"""
        SELECT player_display_name AS player, week, {points_col} AS points
        FROM player_stats
        WHERE season = ? AND season_type = 'REG' AND player_display_name = ANY(?)
        ORDER BY week
        """,
        [season, selected],
    )
    fig = go.Figure()
    for i, name in enumerate(selected):  # selection order fixes the color slot
        d = weekly.filter(pl.col("player") == name)
        fig.add_scatter(
            x=d["week"].to_list(), y=d["points"].to_list(), name=name,
            mode="lines+markers",
            line=dict(width=2, color=SERIES_COLORS[i % 8]),
            marker=dict(size=8),
        )
    fig.update_layout(showlegend=len(selected) > 1)
    st.plotly_chart(base_layout(fig, y_title=f"{scoring} points"), width="stretch")

# ------------------------------------------------------- expected vs actual gap
st.subheader("Expected vs actual fantasy points")
st.caption(
    "From the ffverse opportunity model. A player scoring far below expectation "
    "(negative gap) is a buy-low candidate; far above may be running hot."
)
gap = query(
    """
    SELECT full_name AS player, position, posteam AS team,
           ROUND(SUM(total_fantasy_points), 1)     AS actual,
           ROUND(SUM(total_fantasy_points_exp), 1) AS expected,
           ROUND(SUM(total_fantasy_points_diff), 1) AS gap
    FROM ff_opportunity
    WHERE season = ? AND week <= 18 AND position = ANY(?)
    GROUP BY ALL HAVING SUM(total_fantasy_points_exp) > 50
    ORDER BY gap
    """,
    [season, positions or ["QB", "RB", "WR", "TE"]],
)
col_cold, col_hot = st.columns(2)
with col_cold:
    st.markdown("**Coldest (underperforming expectation)**")
    st.dataframe(gap.head(10), hide_index=True, width="stretch")
with col_hot:
    st.markdown("**Hottest (overperforming expectation)**")
    st.dataframe(gap.tail(10).reverse(), hide_index=True, width="stretch")

# ------------------------------------------------------------- waiver-wire heat
st.subheader("Trending on Sleeper (last 24h)")
trending = query(
    """
    SELECT t.kind, p.full_name AS player, p.position, p.team,
           p.injury_status, t.count AS transactions
    FROM sleeper_trending t
    JOIN sleeper_players p USING (player_id)
    ORDER BY t.count DESC
    """
)
col_add, col_drop = st.columns(2)
with col_add:
    st.markdown("**Most added**")
    st.dataframe(trending.filter(pl.col("kind") == "add").drop("kind").head(10),
                 hide_index=True, width="stretch")
with col_drop:
    st.markdown("**Most dropped**")
    st.dataframe(trending.filter(pl.col("kind") == "drop").drop("kind").head(10),
                 hide_index=True, width="stretch")
