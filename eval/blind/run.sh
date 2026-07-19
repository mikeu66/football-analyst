#!/usr/bin/env bash
# Blind-agent test harness for the nfl-data MCP server.
#
# Runs canned fantasy-user prompts through `claude -p` from an empty directory
# outside the repo, with ONLY the MCP tools available — no source code, no
# CLAUDE.md, no filesystem tools. What gets graded is tool selection and
# argument construction, so transcripts are saved as stream-json and
# summarized by summarize.py.
#
# Usage:
#   ./run.sh                # run all prompts
#   ./run.sh adp-value ...  # run only the given prompt ids
#   MODEL=haiku ./run.sh    # override tester model (default: sonnet)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$HERE" rev-parse --show-toplevel)"
MODEL="${MODEL:-sonnet}"
TIMEOUT="${TIMEOUT:-300}"

RUN_DIR="$HERE/results/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR"

# MCP config that launches the server with an absolute project path, so it
# works from any cwd.
MCP_CONFIG="$RUN_DIR/mcp-config.json"
cat > "$MCP_CONFIG" <<EOF
{
  "mcpServers": {
    "nfl-data": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "$REPO_ROOT", "python", "-m", "mcp_server.server"],
      "env": {}
    }
  }
}
EOF

# Empty cwd outside the repo = no project context leaks into the tester.
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

run_one() {
  local id="$1" prompt="$2"
  echo "=== $id"
  if ! (cd "$WORKDIR" && timeout "$TIMEOUT" claude -p "$prompt" \
      --model "$MODEL" \
      --mcp-config "$MCP_CONFIG" \
      --strict-mcp-config \
      --allowedTools "mcp__nfl-data" \
      --disallowedTools "Bash" "Read" "Write" "Edit" "Glob" "Grep" "WebFetch" "WebSearch" "Task" "NotebookEdit" "TodoWrite" \
      --output-format stream-json --verbose \
      > "$RUN_DIR/$id.jsonl" 2> "$RUN_DIR/$id.stderr"); then
    echo "    FAILED (exit $?) — see $RUN_DIR/$id.stderr"
  fi
}

wanted=("$@")
while IFS= read -r line; do
  [ -z "$line" ] && continue
  id="$(printf '%s' "$line" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
  prompt="$(printf '%s' "$line" | python3 -c 'import json,sys; print(json.load(sys.stdin)["prompt"])')"
  if [ "${#wanted[@]}" -gt 0 ]; then
    case " ${wanted[*]} " in *" $id "*) ;; *) continue ;; esac
  fi
  run_one "$id" "$prompt"
done < "$HERE/prompts.jsonl"

echo
python3 "$HERE/summarize.py" "$RUN_DIR" | tee "$RUN_DIR/SUMMARY.md"
echo
echo "Transcripts: $RUN_DIR"
