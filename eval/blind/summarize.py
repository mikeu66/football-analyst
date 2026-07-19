#!/usr/bin/env python3
"""Summarize blind-run stream-json transcripts into a gradeable report.

For each prompt: the tool-call sequence with arguments, truncated tool
results (enough to spot errors/empty results), and the tester's final answer.
"""
import json
import sys
from pathlib import Path

TRUNC_RESULT = 400
TRUNC_ANSWER = 1200


def load_expectations(run_dir: Path) -> dict[str, str]:
    prompts = run_dir.parent.parent / "prompts.jsonl"
    out = {}
    if prompts.exists():
        for line in prompts.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                out[rec["id"]] = rec.get("expect", "")
    return out


def trunc(text: str, n: int) -> str:
    text = text.strip()
    return text if len(text) <= n else text[:n] + f"… [{len(text)} chars total]"


def summarize(path: Path, expect: str) -> str:
    lines = [f"## {path.stem}", ""]
    if expect:
        lines += [f"*Expected:* {expect}", ""]

    events = []
    for raw in path.read_text().splitlines():
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            continue

    tool_results = {}  # tool_use_id -> content
    for ev in events:
        if ev.get("type") == "user":
            for block in ev.get("message", {}).get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content")
                    if isinstance(content, list):
                        content = " ".join(
                            b.get("text", "") for b in content if isinstance(b, dict)
                        )
                    tool_results[block.get("tool_use_id")] = str(content)

    n_calls = 0
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        for block in ev.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                n_calls += 1
                name = block["name"].removeprefix("mcp__nfl-data__")
                args = json.dumps(block.get("input", {}), ensure_ascii=False)
                lines.append(f"{n_calls}. **{name}** `{trunc(args, 300)}`")
                result = tool_results.get(block.get("id"))
                if result is not None:
                    flag = " ⚠️" if "error" in result.lower()[:120] else ""
                    lines.append(f"   ↳{flag} {trunc(result, TRUNC_RESULT)}")

    final = next((ev for ev in events if ev.get("type") == "result"), None)
    lines.append("")
    if final is None:
        lines.append("**No result event — run likely timed out or crashed.**")
    else:
        status = final.get("subtype", "?")
        turns = final.get("num_turns", "?")
        cost = final.get("total_cost_usd")
        cost_s = f", ${cost:.3f}" if isinstance(cost, (int, float)) else ""
        lines.append(f"*{n_calls} tool calls, {turns} turns, {status}{cost_s}*")
        lines.append("")
        lines.append("**Final answer:**")
        lines.append(trunc(str(final.get("result", "")), TRUNC_ANSWER))
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    run_dir = Path(sys.argv[1])
    expectations = load_expectations(run_dir)
    print(f"# Blind MCP test run — {run_dir.name}\n")
    transcripts = sorted(run_dir.glob("*.jsonl"))
    if not transcripts:
        print("No transcripts found.")
        return
    for path in transcripts:
        print(summarize(path, expectations.get(path.stem, "")))


if __name__ == "__main__":
    main()
