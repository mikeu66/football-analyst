"""Chat frontend for manually testing the nfl-data MCP server.

Drives the Claude Agent SDK (Claude Code's harness, authenticated via your
existing Claude Code login) with ONLY the MCP tools — no repo context, no
filesystem tools — so tool selection behaves like a real end-user session.
Every tool call and its result render inline for inspection.

Run with:
    uv run streamlit run app/Home.py
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import streamlit as st
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Same sandbox as eval/blind/run.sh: MCP tools only, blind to the repo.
DISALLOWED_TOOLS = [
    "Bash", "Read", "Write", "Edit", "Glob", "Grep",
    "WebFetch", "WebSearch", "Task", "NotebookEdit", "TodoWrite",
]

TRUNC_RESULT = 2000

st.set_page_config(page_title="MCP Chat", page_icon="🔧", layout="wide")
st.title("🔧 MCP test chat")
st.caption(
    "Blind agent over the `nfl-data` MCP server — same sandbox as the "
    "`eval/blind` harness, but interactive. Tool calls render inline."
)


def _build_options(model: str, session_id: str | None) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        mcp_servers={
            "nfl-data": {
                "type": "stdio",
                "command": "uv",
                "args": [
                    "run", "--directory", str(REPO_ROOT),
                    "python", "-m", "mcp_server.server",
                ],
            }
        },
        strict_mcp_config=True,
        allowed_tools=["mcp__nfl-data"],
        disallowed_tools=DISALLOWED_TOOLS,
        setting_sources=[],  # no CLAUDE.md, no skills, no hooks
        cwd=st.session_state.workdir,  # empty temp dir, not the repo
        model=model,
        resume=session_id,
        max_turns=25,
    )


async def _run_turn(prompt: str, model: str, session_id: str | None):
    """Run one agent turn; return (events, meta) for rendering."""
    events: list[dict] = []
    results_by_id: dict[str, str] = {}
    meta: dict = {}

    async for msg in query(prompt=prompt, options=_build_options(model, session_id)):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    events.append({"kind": "text", "text": block.text})
                elif isinstance(block, ToolUseBlock):
                    events.append({
                        "kind": "tool",
                        "id": block.id,
                        "name": block.name.removeprefix("mcp__nfl-data__"),
                        "input": block.input,
                    })
        elif isinstance(msg, UserMessage):
            content = msg.content if isinstance(msg.content, list) else []
            for block in content:
                if isinstance(block, ToolResultBlock):
                    results_by_id[block.tool_use_id] = _result_text(block.content)
        elif isinstance(msg, ResultMessage):
            meta = {
                "session_id": msg.session_id,
                "cost": msg.total_cost_usd,
                "subtype": msg.subtype,
            }

    for ev in events:
        if ev["kind"] == "tool":
            ev["result"] = results_by_id.get(ev["id"], "(no result captured)")
    return events, meta


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", item)))
            elif hasattr(item, "text"):
                parts.append(item.text)
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def _render_events(events: list[dict], meta: dict | None) -> None:
    for ev in events:
        if ev["kind"] == "text":
            st.markdown(ev["text"])
        else:
            with st.expander(f"🔧 `{ev['name']}` — {json.dumps(ev['input'])[:120]}"):
                st.code(json.dumps(ev["input"], indent=2), language="json")
                result = ev.get("result", "")
                shown = result[:TRUNC_RESULT]
                st.code(shown, language="json")
                if len(result) > TRUNC_RESULT:
                    st.caption(f"…truncated ({len(result):,} chars total)")
    if meta:
        cost = meta.get("cost")
        cost_s = f" · ${cost:.3f}" if isinstance(cost, (int, float)) else ""
        st.caption(f"{meta.get('subtype', '')}{cost_s}")


# --- session state -----------------------------------------------------------

if "workdir" not in st.session_state:
    st.session_state.workdir = tempfile.mkdtemp(prefix="mcp-chat-")
if "history" not in st.session_state:
    st.session_state.history = []
if "session_id" not in st.session_state:
    st.session_state.session_id = None

with st.sidebar:
    model = st.selectbox("Tester model", ["sonnet", "haiku", "opus"], index=0)
    if st.button("New conversation"):
        st.session_state.history = []
        st.session_state.session_id = None
        st.rerun()
    st.caption(
        "Authenticated via your Claude Code login. The agent sees only the "
        "MCP tool names, descriptions, and schemas — not the repo."
    )

# --- render history + handle input -------------------------------------------

for entry in st.session_state.history:
    with st.chat_message(entry["role"]):
        if entry["role"] == "user":
            st.markdown(entry["text"])
        else:
            _render_events(entry["events"], entry.get("meta"))

if prompt := st.chat_input("Ask a fantasy football question…"):
    st.session_state.history.append({"role": "user", "text": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Agent working (includes MCP server cold start)…"):
            try:
                events, meta = asyncio.run(
                    _run_turn(prompt, model, st.session_state.session_id)
                )
            except Exception as exc:  # surface harness failures in the UI
                events, meta = [{"kind": "text", "text": f"**Error:** {exc}"}], {}
        _render_events(events, meta)

    if meta.get("session_id"):
        st.session_state.session_id = meta["session_id"]
    st.session_state.history.append(
        {"role": "assistant", "events": events, "meta": meta}
    )
