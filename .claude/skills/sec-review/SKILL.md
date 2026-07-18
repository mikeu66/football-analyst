---
name: sec-review
description: Delegated, scored security review. Runs a two-scanner audit (Haiku pattern sweep + Sonnet deep read), independently verifies criticals/highs, and produces a 0–10 scorecard per category plus a markdown report. Use when asked for a security review, security audit, or vulnerability scan. Args - none = quick review of pending changes; "full" = whole-codebase audit; a path = audit that directory only.
---

# Delegated Security Review

You are the orchestrator. Subagents scan; you verify and score. A finding
only affects a score after it has been verified by you or a verifier agent —
scanner reports are leads, not evidence.

The per-category checklists, grep seeds, and severity definitions live in
`categories.md` next to this file. Read it before spawning anything; every
scanner prompt embeds its category's checklist verbatim.

## Modes (from args)

| Arg | Mode | Scope |
|---|---|---|
| _(none)_ | **quick** | Pending changes: uncommitted work plus commits on this branch not on the default branch (`git diff` + `git diff --cached` + `git log main..HEAD`). If there are no pending changes, say so and offer `full`. |
| `full` | **full** | Entire repo (respect `.gitignore`; skip lockfiles except in the dependency category, skip vendored/generated code but note that it was skipped). |
| a path | **full**, scoped | That directory only. Overall score is labeled partial. |

Both modes use the same categories, scoring, and report format so results
are comparable over time.

## Categories

Eight categories, defined in `categories.md`:

1. **INJ** — Injection (SQL/command/path)
2. **SEC** — Secrets & credentials
3. **VAL** — Input validation & deserialization
4. **EXE** — Dangerous code execution (eval/pickle/subprocess)
5. **NET** — Network exposure & SSRF (incl. MCP/app server surface)
6. **ACC** — AuthN/AuthZ & access control
7. **DEP** — Dependencies & supply chain
8. **CFG** — Configuration & hardening

Every category gets a score every run, even if the scope contains nothing
relevant (then it scores 10 with the note "nothing in scope").

## Model routing

| Role | Model | Why |
|---|---|---|
| **Sweep scanner** (one agent): SEC, EXE, CFG, DEP inventory | `haiku` | checklist + grep seeds, zero ambiguity |
| **Deep scanner** (one agent): INJ, VAL, NET, ACC, DEP risk assessment | `sonnet` | one full read answers all judgment categories — the taint path traced for injection is the same one that answers validation and access control |
| Verifier for every critical/high | `sonnet` — but **never the agent that reported the finding** | independent check |
| Final gate on criticals/highs, scoring, report | you | do not delegate the score |

All scanner and verifier agents are **read-only**: instruct them to never
edit, create, or delete files, and never run state-changing commands.

## Execution

### Quick mode

1. Build the diff and the list of touched files yourself; include the full
   current content of touched files in scope (a diff hides the context that
   makes a line dangerous).
2. Spawn **one Sonnet scanner** covering all eight categories over that
   scope, prompt built from the template below with all checklists attached.
   If the diff is large (>~15 files), split into two scanners by file list,
   never by overlapping files.
3. Verify criticals/highs yourself (step "Gate" below), score, report.

### Full mode

1. Map the repo yourself first: entry points (app, MCP server, pipeline),
   where untrusted input enters, what talks to the network. Put this map in
   every scanner prompt so agents don't re-derive or mis-derive it.
2. **Wave 1 — two scanners in parallel** (routing table above). Each prompt
   names the exact directories in scope and embeds its categories'
   checklists verbatim. The Haiku sweep additionally gets the grep seeds
   from `categories.md` and this rule: "Run the seeds, then read every
   hit's surrounding function before reporting. A grep hit alone is not a
   finding."
3. **Wave 2 — verification.** Batch every critical/high (from either
   scanner) to one Sonnet verifier agent (never the reporter). Verdict per
   finding: CONFIRMED / FALSE POSITIVE / DOWNGRADE-UPGRADE with evidence.
   Medium/low findings skip the verifier: they stand as reported if they
   carry quoted evidence, and are dropped (listed as unverified) if they
   don't.
4. **Gate — you.** Personally re-check every finding that is critical or
   high after verification: read the code at the cited line, confirm the
   attack path is real in this codebase (not just theoretically bad API
   use). You may run read-only commands to confirm. Anything you can't
   confirm drops to "unverified" and is excluded from scoring.

### Finding format (require verbatim in every scanner prompt)

Each finding: `[CATEGORY] severity | file:line | one-line defect |
evidence (quoted code) | attack path (concrete input → concrete impact) |
suggested fix`. No finding without quoted evidence. "Could be risky" is
not a finding — put style concerns in a NOTES section, unscored.

## Scoring

Per category, using **verified findings only**:

- Start at 10. Deduct: critical −7, high −4, medium −2, low −1, info −0.
- Severity caps regardless of arithmetic: any critical caps the category at
  **3**; any high caps it at **6**. Floor is 0.
- **Overall = the minimum category score** (weakest link), never the
  average. A 9.5 average with one SQL injection is not a 9.5 codebase.

Severity definitions are in `categories.md`; apply them, don't improvise.

## Report

1. **Terminal scorecard** in your final message: table of category → score
   → verified findings count (by severity) → one-line worst issue; then
   overall score, mode/scope, and the top 3 fixes in priority order.
2. **Report file** at `docs/security-reviews/YYYY-MM-DD-<quick|full>.md`
   (append `-2` etc. if it exists): scorecard, scope and commit SHA, model
   roster, every verified finding in full format, unverified/false-positive
   findings in a separate section with why they were excluded, and the
   NOTES section. The report is the durable artifact; the terminal message
   is the summary of it.

## Rules

- Scanner reports are not evidence. Scores come only from what survived
  verification and your gate.
- Never let a model verify its own findings.
- If the Haiku sweep returns garbage (no evidence quotes, vague findings),
  rerun it once on Sonnet rather than arguing with it.
- Do not fix anything during the review. Report only; offer fixes after.
- Deduplicate before scoring: the same root cause hit in N places is one
  finding with N locations, scored once at the highest applicable severity.
- Report every category every time — a category with no scope scores 10
  with a note, so score history stays comparable across runs.
