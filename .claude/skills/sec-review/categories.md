# Security Review Categories — Checklists & Severity

Reference for the `sec-review` skill. Each scanner prompt embeds its
category's section verbatim. Grep seeds are starting points for Haiku
agents — a hit must be read in context before it becomes a finding, and
the checklist is not limited to what the seeds catch.

## Severity definitions (apply to every category)

- **critical** — Remotely or trivially exploitable with serious impact:
  arbitrary code/SQL execution on untrusted input, leaked live credential,
  auth bypass on an exposed surface. "An attacker who can reach X does Y"
  with no unlikely preconditions.
- **high** — Real vulnerability with a plausible attack path but requiring
  some precondition (local access, a second bug, a misconfigured deploy),
  or serious impact limited in blast radius.
- **medium** — Genuine weakness unlikely to be exploitable alone: missing
  validation behind a trusted boundary, weak defaults, overly broad
  permissions, secrets in files that are gitignored but plaintext.
- **low** — Hardening gap or defense-in-depth miss with no identified
  attack path.
- **info** — Worth recording, not a weakness (e.g. dependency one major
  version behind with no known CVE). Never affects score.

When severity depends on deployment context you can't observe (e.g. "is
this server ever exposed beyond localhost?"), score the severity for the
documented/likely deployment and state the assumption in the finding.

## INJ — Injection (Sonnet)

SQL, command, and path injection. Trace where each query/command/path is
built, not just where it executes.

- Every SQL call site: is user- or file-derived data interpolated (f-string,
  `%`, `+`, `.format`) into the query text? Parameterized (`?`) is fine;
  interpolated identifiers (table/column names) need an allowlist.
- MCP tool parameters that reach SQL: these are untrusted even if "the
  caller is an LLM".
- `subprocess`/`os.system` with `shell=True` or string commands built from
  variables.
- File paths built from external names (`../` traversal); check for
  normalization + containment, not just `os.path.join`.

Seeds: `execute(f"`, `execute("...% `, `.format(`, `shell=True`,
`os.system`, `read_csv|read_parquet` with variable paths.

## SEC — Secrets & credentials (Haiku sweep)

- Hardcoded API keys, tokens, passwords, connection strings in source,
  config, notebooks, test fixtures, and `.env`-style files.
- Secrets in git history is out of scope for scanners; the orchestrator
  notes it as a follow-up if a plaintext secret file is found tracked.
- Secrets logged, printed, or embedded in error messages / reports.
- Weak handling: secrets read then passed through many layers, written to
  temp files or caches.

Seeds: `api_key`, `apikey`, `secret`, `token`, `password`, `passwd`,
`Authorization`, `Bearer `, `AKIA`, `sk-`, `-----BEGIN`, `.env`.
Verification for a suspected live credential: judge by format/entropy and
placement only — never call the provider to test it.

## VAL — Input validation & deserialization (Sonnet)

- Untrusted deserialization: `pickle`, `yaml.load` without `SafeLoader`,
  `eval` on data, unvalidated `json` fed into dataclasses/queries.
- Data pipeline inputs: downloaded files parsed with permissive parsers;
  schema/type validation before data reaches SQL or the app.
- Missing bounds/type checks on anything crossing a trust boundary (HTTP
  params, MCP tool args, CLI args used in queries).
- Integer/size limits on user-controlled query knobs (LIMIT, date ranges)
  that could turn into resource exhaustion.

Seeds: `pickle.load`, `yaml.load(`, `eval(`, `literal_eval`, `json.loads`,
`request.`, `input(`.

## EXE — Dangerous code execution (Haiku sweep)

- `eval`/`exec`/`compile` on anything non-literal.
- `pickle`/`shelve`/`marshal` loads from files that could be replaced or
  downloaded.
- Dynamic imports (`importlib`, `__import__`) with variable names.
- Notebook or script execution of fetched code; `curl | sh` patterns in
  setup scripts.

Seeds: `eval(`, `exec(`, `compile(`, `pickle.`, `marshal`, `shelve`,
`importlib`, `__import__`, `getattr(` with variable attribute on modules.

## NET — Network exposure & SSRF (Sonnet)

- Bind addresses: `0.0.0.0` vs `127.0.0.1` for the app and MCP server;
  what's actually reachable and is there any auth in front of it.
- SSRF: any fetch/download where the URL or host is influenced by external
  data; redirects followed; internal address ranges reachable.
- Plain HTTP for anything carrying data or credentials; TLS verification
  disabled (`verify=False`).
- Data egress: does anything send local data to third parties beyond what
  the feature requires.

Seeds: `0.0.0.0`, `verify=False`, `http://`, `requests.get`, `urlopen`,
`httpx`, `allow_redirects`.

## ACC — AuthN/AuthZ & access control (Sonnet)

- Exposed surfaces (HTTP routes, MCP tools) — which operations are
  read-only vs mutating, and what prevents an unintended caller from
  invoking the mutating ones.
- MCP server: tools that write files, execute SQL DDL/DML, or shell out are
  high-consequence; check what constrains them (read-only DB connection,
  statement allowlist, path allowlist).
- Multi-user assumptions: anything keyed by user identity taken from the
  request without verification.
- File permissions on created secrets/DBs (world-readable key files).

Seeds: `add_tool`, `@app.`, `@router`, `route(`, `chmod`, `0o7`, `CREATE `,
`DELETE `, `DROP `, `INSERT `, `UPDATE `.

## DEP — Dependencies & supply chain (Haiku sweep inventory, Sonnet deep assessment)

- Inventory (Haiku): list direct deps and pins from `pyproject.toml` /
  lockfile; flag unpinned deps, git/URL dependencies, and anything
  obviously abandoned or typo-suspicious.
- Assessment (Sonnet): known-CVE check against training knowledge for the
  pinned versions — report as "as of knowledge cutoff", severity capped at
  high unless confirmed by running an installed audit tool (`pip-audit`,
  `uv audit`) read-only. Do not install new tools for this.
- Install-time code execution: setup scripts, postinstall hooks.

Seeds: `git+`, `http`, in dependency files; `pip install` in scripts/docs.

## CFG — Configuration & hardening (Haiku sweep)

- Debug/development flags on by default (`debug=True`, verbose error pages,
  stack traces to clients).
- CORS `*`, permissive hosts, missing timeouts on outbound calls.
- `.gitignore` coverage: are `.env`, DB files, credentials dirs ignored;
  is anything sensitive currently tracked (`git ls-files` against secret
  patterns).
- Logging: sensitive values at info level; logs written world-readable.
- Temp file handling: predictable paths, `mktemp`-style races.

Seeds: `debug=True`, `DEBUG`, `CORS`, `allow_origins`, `\*`, `tempfile`,
`mktemp`, `timeout=None`.
