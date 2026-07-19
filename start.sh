#!/usr/bin/env bash
# Start the Streamlit frontend and the nfl-data MCP server together.
#
# The MCP server communicates over stdio, so it's not useful as a bare
# standalone process for a client to attach to over a pipe from this script;
# it's started here mainly so `claude mcp add` / manual stdio clients have
# something running and logging during a dev session. The Streamlit "MCP
# Chat" page spawns its own copy of the server per query regardless.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

mkdir -p data

MCP_LOG="data/mcp_server.log"
STREAMLIT_PID=""
MCP_PID=""

cleanup() {
    echo "Shutting down..."
    [[ -n "$STREAMLIT_PID" ]] && kill "$STREAMLIT_PID" 2>/dev/null || true
    [[ -n "$MCP_PID" ]] && kill "$MCP_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting MCP server (logging to $MCP_LOG)..."
uv run python -m mcp_server.server >"$MCP_LOG" 2>&1 &
MCP_PID=$!

echo "Starting Streamlit frontend..."
uv run streamlit run app/Home.py &
STREAMLIT_PID=$!

wait "$STREAMLIT_PID"
