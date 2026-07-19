# Plan: Bring-Your-Own-Key (BYOK) MCP Chat for Public Hosting

## Goal
Let the MCP Chat page (`app/pages/1_MCP_Chat.py`) be used by visitors on a
publicly hosted deployment, each paying for their own Anthropic usage via
their own API key — instead of the current setup, which rides the host's
personal Claude Code login for every request.

## Why
The Claude Agent SDK (`claude_agent_sdk.query()`) authenticates via the
local Claude Code OAuth session. That's fine for personal/local use, but on
a shared public host every visitor's request would draw on the host's own
subscription — no per-user billing, no usage isolation. Swapping to a
plain API key per session fixes this without needing any billing
infrastructure on the host's side.

## Changes

### 1. Add a key-input field (Streamlit sidebar)
- `st.text_input("Anthropic API Key", type="password")` in the sidebar.
- Store only in `st.session_state` — never written to disk, never logged.
- If empty, show a message and disable the chat input rather than silently
  falling back to any host-side credential.
- Optionally: pre-fill from `os.environ.get("ANTHROPIC_API_KEY")` when run
  locally, so the host's own testing isn't disrupted.

### 2. Replace `claude_agent_sdk.query()` with the `anthropic` SDK
- Swap the Agent SDK's built-in harness for `anthropic.Anthropic(api_key=...)`
  constructed fresh per request from the session-provided key.
- Use the **Tool Runner** (`client.beta.messages.tool_runner`) to keep an
  agentic loop without hand-rolling one — pass the MCP server's tools in
  via the MCP-to-tool conversion helpers (`anthropic.lib.tools.mcp`,
  requires `pip install anthropic[mcp]`).
- This replaces `ClaudeAgentOptions`/`DISALLOWED_TOOLS`/`allowed_tools`
  wiring with an explicit tool list built from the `nfl-data` MCP server
  only (same sandboxing intent: blind to the repo, MCP tools only).

### 3. Rebuild event rendering for the new response shape
- Tool Runner yields `BetaMessage` objects, not the Agent SDK's
  `AssistantMessage`/`ToolUseBlock`/`ToolResultBlock` stream — update
  `_run_turn` / `_render_events` to read `response.content` blocks
  (`text`, `tool_use`, `tool_result`) from the new SDK's types instead.
- Preserve existing UX: inline expander per tool call, truncated JSON
  result, cost/session caption at the bottom (`response.usage` gives
  token counts; per-request cost needs a simple $/token calc since the
  key is user-supplied, not tied to a Claude Code session cost figure).

### 4. Session-scoped conversation state
- Keep `st.session_state.history` as-is for chat history.
- Session/model selection (`sonnet`/`haiku`/`opus`) maps directly to
  Anthropic model IDs (`claude-sonnet-5`, `claude-haiku-4-5`,
  `claude-opus-4-8`) instead of the Agent SDK's shorthand.

### 5. Guardrails before publishing
- Confirm `.gitignore` excludes any `.env` / local key files (already
  does per current `.gitignore`).
- Add a short README note in the MCP Chat page: "your key is used only
  for this session and never stored."
- No server-side logging of the key value (avoid printing exceptions
  that might echo it back).

## Out of scope (for this change)
- Any billing/metering layer for a "host pays" model — BYOK sidesteps
  that entirely.
- Rate limiting or abuse protection on the hosted deployment (separate
  concern if this goes fully public).

## Open questions for the user
- Keep the Agent SDK for local/dev use (behind a toggle) and only use
  BYOK + Tool Runner in the hosted deployment? Or fully replace it?
- Any interest in capping session token usage client-side (e.g. warn at
  N requests) to protect visitors from accidental cost overruns?
