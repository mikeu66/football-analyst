---
name: nfl-mcp-playbook
description: Playbook for building and maintaining the NFL DuckDB MCP server. Use when asked to execute the MCP server plan, "build the MCP server", or work on MCP server tasks.
---

# MCP Server Playbook

The task breakdown, file layout, and acceptance criteria live in
`docs/mcp-server-plan.md` — read it first, then execute it. Do not redesign
the plan; if a task's spec turns out wrong, fix the plan file first.

## Execution model

Do the work inline, yourself. At this codebase's size (~800 lines in
`mcp_server/`), subagent fan-out costs more in cold-start context loading
and duplicated verification than it saves. Before touching any file, read
`.claude/skills/nfl-data-context/SKILL.md` — it is authoritative;
do not re-derive schema facts.

Delegate only when both are true:

- there are 3+ independent tasks touching disjoint files, **and**
- wall-clock time matters more than cost (the user asked for parallelism,
  or the tasks block other work).

If you do delegate: `sonnet` for anything correctness-sensitive, one agent
per file, never two concurrent agents owning the same file (the plan's
module split exists for this; preserve it), and every agent's prompt names
its task ID, acceptance criteria copied from the plan, and the file it owns
("Do not edit any other file"). Subagent reports are not evidence — gates
below are checks **you** run either way.

## Gates (run after every task, delegated or not)

1. Run the task's acceptance check from the plan as a command before
   calling the task done.
2. Schema claims (new joins, new columns, semantics doc edits): spot-check
   against the live DB, not against memory or docs.
3. New or changed tools: import the module and make one real-data call.
4. Test tasks gate on `uv run pytest` green.
5. End-to-end changes: exercise the server over stdio and answer one real
   analysis question using only its tools.

Keep a running status in your final message: task → outcome → gate result.
