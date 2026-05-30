# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

AgentTrace — local token-waste profiler for AI coding agents (Claude Code, Aider). Sits as a transparent proxy between agent and provider API. Captures every request/response, identifies re-read file content, redundant scans, and context-bloat. Reports "X% of billed input tokens were wasted re-reading unchanged files."

Implementation plan: `PLAN.md` (master reference). Locked architecture decisions are D1–D13 in that doc. Do not deviate from locked decisions without updating the plan.

---

## Commands

### Setup (contributors)
```bash
poetry install --with dev,test   # install all deps including dev/test groups
pre-commit install                # wire local hooks (mirrors CI lint+types+secret-scan)
```

### Daily dev
```bash
poetry run pytest                          # full suite
poetry run pytest tests/unit/adapters/     # single module
poetry run pytest -k "test_anthropic"      # single test by name
poetry run pytest --cov=agenttrace --cov-branch --cov-fail-under=85
poetry run ruff check . && poetry run ruff format --check .
poetry run mypy --strict agenttrace
```

### Run proxy locally
```bash
poetry run agenttrace start --port 8788    # prints per-agent setup snippet, then blocks
poetry run agenttrace report               # renders static HTML report for latest session
poetry run agenttrace export --session LATEST --format json
```

### CI checks (run before PR)
```bash
poetry check --lock                        # lockfile drift check
poetry run pip-audit                       # vuln scan
poetry build && pip install dist/*.whl     # smoke: wheel installs and CLI starts
```

---

## Architecture

### Hot-path invariant
The proxy handler does **only**: read bytes → open upstream stream → pipe both directions → enqueue reference-copy for capture. Zero parsing, zero tokenization, zero DB I/O on the hot path. All semantic work happens in Writer/Analysis tasks. This is the <10ms p95 budget guarantee.

### Tee-ing proxy (capture layer)
`agenttrace/proxy/` — Starlette 1.0 + uvicorn. Each request: forward upstream via httpx `client.stream`, relay `response.aiter_raw()` chunk-for-chunk to client, tee each chunk onto an `asyncio.Queue[CaptureEvent]`. Fail-open: any capture error is caught and logged; proxy keeps forwarding untouched.

### Writer task (single writer)
`agenttrace/capture/` — single long-lived consumer of the bounded capture queue. Only task that writes to SQLite (WAL mode). Batches: flush on 200 events OR 500ms. Queue overflow = drop-and-count (metric `capture.dropped`), never stall hot path.

### Two-tier adapters
- `agenttrace/adapters/providers/` — wire-schema normalization per provider (Anthropic, OpenAI). Extracts authoritative usage including cache breakdown.
- `agenttrace/adapters/agents/` — semantic classification per agent (claude_code, aider). Classifies tool calls (FILE_READ/GLOB/EDIT/OTHER), extracts file payloads, strips volatile hash (`normalize_volatile`).

Claude Code injects a per-request varying token into the system prompt. `claude_code.py` strips it via `VOLATILE_HASH_RE` before hashing. If regex matches 0 or >1 sites: log `volatile_strip_ambiguous`, hash as-is (never silently strip wrong content).

### Analysis (off hot path)
`agenttrace/analysis/` — runs on-demand or post-session. DuckDB attaches SQLite read-only (`ATTACH ... TYPE sqlite, READ_ONLY`). Sessionizer uses longest-common-prefix matching of hashed message arrays. Differ keys re-reads by `(path, content_hash)`. Waste attributor applies cache-net cost formula (Appendix C of PLAN.md).

### Store
SQLite (stdlib `sqlite3`) for capture/OLTP. DuckDB for analytics (read-only attach). Store records **requests**; turns and sessions are derived views computed in DuckDB, never denormalized into SQLite. Schema migrations: numbered SQL files in `migrations/` applied in order on startup.

### Hashing convention (Appendix E.1)
SHA-256 hex of UTF-8 bytes everywhere. JSON canonicalized via `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` before hashing. No other hash used anywhere.

### Provider/agent detection (Appendix E.3)
1. Provider: `/v1/messages` or `anthropic-version` header → anthropic; `/v1/chat/completions` or `/v1/responses` → openai; else unknown.
2. Agent: UA substrings in `adapters/agents/markers.toml` (case-insensitive). `claude-cli/` prefix → claude_code. Unknown UA → `agent=unknown` (provider metrics only, no waste attribution).

### Pricing
`pricing/defaults.toml` — TOML price table seeded from LiteLLM dataset. Unknown model → cost `null` with badge, never guessed. User-overridable via `AGENTTRACE_PRICE_TABLE`.

### P0 vs P1 presentation
- P0: Jinja2 → single self-contained `report.html` (inlined CSS/JS/SVG). No network fetches.
- P1: React 18 + TypeScript + Vite in `webui/` (separate `package.json`). Recharts + Tailwind. Local read-only HTTP API.

---

## Engineering constraints

### Type safety
All public API functions fully type-hinted. `mypy --strict` must pass. No `Any` without explicit ignore comment with reason.

### No blocking in async
Zero `time.sleep`, zero synchronous file I/O, zero synchronous DB calls inside async code. Resources via context managers. No bare `except:` — always catch specific exception types.

### Credentials
Never persisted. Never logged. Forwarded in-memory only. structlog processor redacts any header/body field matching credential regexes before logging. `secret-scan` (gitleaks) is a required CI gate.

### HTTP forwarding rules (Appendix E.2)
Forward verbatim: method, path, query, body bytes. Always preserve: `authorization`, `x-api-key`, `anthropic-version`, `anthropic-beta`, `content-type`, `x-*`. Strip hop-by-hop headers. Never set `accept-encoding` to something client didn't send.

### No double-retry
Single httpx client wrapper owns all timeouts and retry policy. No per-call retry in the proxy — agents retry themselves.

### Repository pattern
SQLite/DuckDB access isolated from analysis logic. DTOs never expose raw store rows. No mixed I/O + business logic.

### Phased delivery
Active phase is P0 (MVP). See PLAN.md §7 for P0 task list and exit criterion. Do not implement P1/P2 features until P0 exit criterion is met.

### Definition of done (P0, Appendix E.11)
Task is done when: (a) public functions fully type-hinted, pass `mypy --strict`; (b) error paths AND happy paths have tests; (c) no bare `except`, no blocking calls in async, resources via context managers; (d) capture/proxy tasks have integration test proving byte-exact passthrough AND fail-open under injected capture error; (e) code review against `BASE_SHA..HEAD_SHA` completed with no unresolved Critical/Important issues.

### Coverage gate
≥85% lines, ≥75% branches on `analysis/`, `adapters/`, `tokens/`, `capture/`. Hot-path proxy covered by integration + load tests.

---

## Key config env vars

| Var | Default | Purpose |
|-----|---------|---------|
| `AGENTTRACE_PORT` | 8788 | Proxy listen port |
| `AGENTTRACE_DB_PATH` | `~/.agenttrace/store.db` | SQLite store |
| `AGENTTRACE_UPSTREAM_ANTHROPIC` | `https://api.anthropic.com` | Upstream URL |
| `AGENTTRACE_UPSTREAM_OPENAI` | `https://api.openai.com` | Upstream URL |
| `AGENTTRACE_STORE_BODIES` | true | Toggle full body persistence |
| `AGENTTRACE_QUEUE_MAXSIZE` | 10000 | Capture queue bound |
| `AGENTTRACE_SESSION_GAP_SECONDS` | 1800 | Session time-gap bound |

No credentials in config. Proxy reads them from forwarded request headers only.
