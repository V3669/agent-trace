# AgentTrace — Master Implementation Plan (Phase 2)

**Status:** Approved-decisions build plan, post critical re-validation
**Date:** May 30, 2026
**Scope:** Implements PRD v0.1 against the Phase 1 Decision Matrix (D1–D13) as refined during validation.
**Owner:** Vigi · **Audience:** implementing engineer(s) / portfolio reviewers

---

## 0. Reading guide & locked decisions

This plan is exhaustive by intent. Section 1 fixes the stack and versions. Sections 2–6 specify the system layer-by-layer with data models and algorithms. Section 7 is the phased task breakdown (P0→P2). Section 8 is the testing strategy. Section 9 is CI/CD for every push and PR. Section 10 is the stress-testing regime. Section 11 tracks risks. Appendices hold the SQLite DDL, config schema, and the formal waste definition.

**Locked decisions carried from Phase 1 (as refined):**

| ID | Decision | Refinement applied in Phase 2 |
|----|----------|-------------------------------|
| D1 | Custom transparent capture proxy | Implemented as a **tee-ing** proxy: bytes forwarded immediately, a copy tee'd to an out-of-band parser. Never buffers the hot path. |
| D2 | Async Python | **Raw Starlette 1.0 + httpx** on the hot path (not FastAPI). |
| D3 | OTel `gen_ai.*` as external contract | Store provider-verbatim in P0; `gen_ai.*` becomes an **export** format in P1+, never the live store schema. |
| D4 | Two-tier adapters (provider + agent) | Agent adapters are first-class plugins. |
| D5 | v1 targets: Claude Code (Anthropic) + Aider (OpenAI-format) | Cursor explicitly deferred (interception unverified). |
| D6 | Provider-reported usage primary; tiktoken fallback labeled low-confidence | `count_tokens` API forbidden on default path (breaks zero-egress). |
| D7 | Cache-net waste definition | Re-sent context costed at actual billed (cache-read) rate. |
| D8 | SQLite (write) + DuckDB (analytics) | SQLite **WAL + single-writer queue**; DuckDB attaches SQLite **read-only**. |
| D9 | Web UI | **P0 = static self-contained HTML report**; **P1 = React SPA**. |
| D10 | LCP-on-message-hash sessionization | Volatile per-request hash normalized before hashing; compaction/microcompact are session events. |
| D11 | In-memory-only API key custody | Credentials never written to store, never logged. |
| D12 | Single instance, per-request tenancy tags | — |
| D13 | Schema/convention version pinning | `schema_version` column + migration gate. |

---

## 1. Technology Stack & Pinned Versions

Versions verified current as of May 30, 2026. Pin with lower bounds in `pyproject.toml`, lock exact versions in `uv.lock`.

### 1.1 Runtime & language
- **Python 3.12** (floor 3.11; 3.12 is the development/CI target). Rationale: 3.11+ exception groups and `asyncio.TaskGroup`; 3.12 perf and f-string improvements.
- **Dependency & packaging manager: Poetry `>=2.1,<3`.** Use the **PEP 621 `[project]` table** (PEP 508 dependency strings), not the legacy `[tool.poetry.dependencies]` table. `poetry.lock` is committed. Build backend is `poetry-core`. Runtime deps live in `[project.dependencies]`; dev/test/web tooling lives in Poetry **dependency groups** (`[tool.poetry.group.dev.dependencies]`, `.test`, `.web`). Exact, copy-pasteable `pyproject.toml` skeleton is given in Appendix D.
- **One-command install (FR-4.1):** end users install the published wheel with `pipx install agenttrace` (or `pip install agenttrace`); the CLI entry point `agenttrace` is registered via `[project.scripts]`. Contributors use `poetry install --with dev,test`. Do **not** instruct end users to install Poetry — Poetry is a contributor tool only.

### 1.2 Capture layer (hot path)
- **Starlette `>=1.0,<2`** — ASGI app/routing/streaming. Stable 1.0 (Mar 22 2026), anyio-only dependency.
- **uvicorn `>=0.34`** — ASGI server (HTTP/1.1 + chunked/SSE).
- **httpx `>=0.28`** — async upstream client with native streaming (`client.stream`) and HTTP/2.
- **anyio** — transitively via Starlette; used directly for task groups/queues.

### 1.3 Store & analytics
- **SQLite** — via stdlib `sqlite3` (WAL mode). Capture/OLTP path.
- **DuckDB `>=1.5,<2`** (PyPI `duckdb`, Production/Stable) — analytical engine; attaches the SQLite file read-only via the `sqlite_scanner`.

### 1.4 Modeling, config, CLI
- **Pydantic `>=2.7`** + **pydantic-settings `>=2.3`** — typed turn/DTO models and env-driven config (anti-pattern fix: no hard-coded config/secrets).
- **Typer `>=0.12`** (or **click**) — CLI (`agenttrace start|report|compare|export`).
- **structlog `>=24.1`** — structured logging; redaction filter that drops any field matching credential patterns.

### 1.5 Tokenizer fallback (no-egress)
- **tiktoken `>=0.7`** — approximation only, every estimate tagged `confidence="low"`. Never the Anthropic `count_tokens` API on the default path (egress + latency).

### 1.6 Presentation
- **P0:** Jinja2 template → single self-contained HTML file (inlined CSS/JS, charts via inline SVG or a vendored micro-charting snippet). No network fetches.
- **P1:** **React 18 + TypeScript + Vite**, **Recharts** for timeline/bars, served locally from the same process reading the store. Tailwind for styling.

### 1.7 Dev tooling / quality gates
- **ruff `>=0.6`** — lint + format (replaces black/isort/flake8).
- **mypy `>=1.10`** (strict) — full type checking; complements the "type hints on all public APIs" rule.
- **pytest `>=8.2`** + **pytest-asyncio `>=0.23`** + **pytest-cov** — test runner/coverage.
- **respx `>=0.21`** — httpx mock transport for upstream simulation.
- **hypothesis `>=6.100`** — property-based tests for the diffing/sessionization engine.
- **pip-audit** — dependency vulnerability scan in CI (run against the Poetry-installed environment).
- **pre-commit `>=3.7`** — local enforcement mirror of CI.

---

## 2. System Architecture (concrete)

```
              ┌──────────────────────── agenttrace (single process) ─────────────────────────┐
  Agent  ───▶ │  Capture (Starlette/uvicorn)                                                  │
 (CC/Aider)   │    ├─ tee-ing proxy handler  ──hot path──▶ httpx stream ──▶ Provider API      │
   set        │    │     • forward request bytes verbatim, inject nothing                     │
 BASE_URL     │    │     • duplicate (req, streamed resp) onto an in-mem capture channel       │
   to ───────▶│    │     • fail-open: on ANY internal error, keep forwarding, log non-blocking │
 localhost    │    └─ capture channel ──▶ asyncio.Queue ──▶ single Writer task ──▶ SQLite(WAL) │
              │                                                                                │
              │  Analysis (off hot path; on-demand or post-session)                           │
              │    DuckDB ATTACH sqlite (READ_ONLY) ──▶ sessionizer ▶ diff ▶ waste attributor  │
              │                                                                                │
              │  Presentation                                                                  │
              │    P0: render static HTML report  |  P1: serve React SPA + read API            │
              └────────────────────────────────────────────────────────────────────────────-─┘
```

**Hot-path invariant:** the proxy handler performs only: read incoming stream → open upstream stream → pipe both directions → enqueue a *reference-copy* of bytes for capture. No parsing, no tokenization, no DB I/O occurs on the hot path. All semantic work happens in the Writer/Analysis tasks. This is how the <10 ms p95 budget (NFR) is met while satisfying FR-1.3/1.4.

### 2.1 Module layout

```
agenttrace/
  proxy/          # Starlette app, tee handler, SSE pipe, fail-open guard
  capture/        # capture channel, Writer task, SQLite schema + migrations
  adapters/
    providers/    # anthropic.py, openai.py  (wire-schema normalization)
    agents/       # claude_code.py, aider.py (tool-call classification)
  analysis/       # sessionizer, differ, waste_attributor, ranker, duckdb_queries
  tokens/         # usage_parser (authoritative), estimator (tiktoken, low-confidence)
  pricing/        # price_table loader + defaults (seeded from LiteLLM model_prices)
  report/         # jinja templates (P0), export (md/json, gen_ai.* in P1)
  webui/          # P1 React SPA + read-only HTTP API
  cli.py          # typer entrypoints
  config.py       # pydantic-settings
  models.py       # Pydantic DTOs (Turn, Session, WasteReport, ...)
```

Architectural anti-pattern guards applied: repository pattern isolates SQLite/DuckDB access from analysis (no mixed I/O + business logic); DTOs never expose raw store rows; retry/timeout centralized in one httpx client wrapper (no scattered/double retry).

---

## 3. Capture Layer Specification

### 3.1 Interception (D1, D12, R-CACHE-HASH)
- **Mechanism:** base-URL override. For Claude Code: `ANTHROPIC_BASE_URL=http://127.0.0.1:<port>` (+ `ANTHROPIC_AUTH_TOKEN` pass-through). For Aider: `--openai-api-base`/env. `agenttrace start` prints the exact export snippet per agent (FR-4.1).
- **TLS:** localhost plain-HTTP listener; upstream TLS handled by httpx. No MITM cert needed for base-URL-override agents (documented fallback for others is out-of-scope for v1).
- **Auth custody (D11):** the agent's `Authorization`/`x-api-key`/`ANTHROPIC_AUTH_TOKEN` header is forwarded **verbatim, in memory only**. A structlog processor redacts any header/body field matching credential regexes before logging. Credentials are **never** persisted.
- **Tenancy (D12):** each request tagged `{agent_id, provider, recv_ts, conn_id}` at ingress for downstream sessionization.

### 3.2 Streaming pipe (FR-1.2, FR-1.6 reliability)
- Use httpx `client.stream("POST", upstream, ...)` and relay `response.aiter_raw()` to the client `StreamingResponse` **chunk-for-chunk**. SSE framing preserved byte-exact.
- A tee wraps the async iterator: each chunk is yielded downstream *and* appended (by reference) to a per-request capture buffer. Capture never blocks the yield.
- **Fail-open (NFR reliability):** the entire capture path is wrapped so that any exception (parse, queue full, disk error) is caught, logged non-blocking, and the proxy continues forwarding untouched. A dropped capture must never surface to the agent.

### 3.3 Writer task (D8 concurrency)
- Single long-lived consumer of an `asyncio.Queue[CaptureEvent]`. Only this task writes to SQLite. WAL mode enabled at open (`PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`).
- Backpressure: bounded queue; on overflow, drop-and-count (metric `capture.dropped`) rather than stalling the hot path.
- Batched commits (N events or T ms, whichever first) to bound fsync cost.

### 3.4 Usage parsing (FR-1.4, D6)
- Provider adapter extracts authoritative usage from the response:
  - **Anthropic:** `usage.input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` (per-block where present). On streaming, the terminal `message_delta`/`message_stop` carries final usage.
  - **OpenAI:** `usage.prompt_tokens`/`completion_tokens` (+ `prompt_tokens_details.cached_tokens` when present); request `stream_options.include_usage` reliance noted, with reconstruction fallback.
- **Streaming-interruption semantics (closes G9):** if a stream terminates before a usage frame, mark the turn `usage_source="partial"`, persist whatever arrived, and flag for estimator backfill (low-confidence).

### 3.5 Request/turn modeling (closes G1)
- **Request** = one HTTP round-trip (one row in `requests`).
- **Turn** = one agent step; a turn groups ≥1 requests caused by the same step (tool-use loops produce multiple requests). Turn grouping derived in analysis from sessionization, not at capture time.
- The store records **requests**; turns and sessions are derived views. This separation is explicit to avoid the PRD's request/turn conflation.

---

## 4. Adapter Layer (D4) — closes G2

Two distinct interfaces, both plugin-discoverable:

### 4.1 ProviderAdapter (wire schema)
```
detect(request_headers, body) -> bool
normalize_request(body) -> CanonicalRequest      # messages[], system, tools[]
parse_usage(response | stream_frames) -> Usage   # incl. cache breakdown
```

### 4.2 AgentAdapter (semantic classification)
```
classify_tool_calls(canonical_request) -> list[ToolEvent]
# identifies FILE_READ, GLOB/SCAN, EDIT, OTHER per the agent's tool vocabulary
extract_file_payloads(tool_results) -> list[FileReadEvent]  # path + content hash
normalize_volatile(canonical_request) -> CanonicalRequest   # strip per-request hash
```
- **claude_code.py:** recognizes `Read`/`view`/`Glob`/`Grep`/`Edit`; **strips the injected per-request system-prompt hash** and accounts for microcompact (R-CACHE-HASH) before any hashing/diffing.
- **aider.py:** recognizes Aider's file-in-context blocks and shell/cat reads.
- Unknown agents degrade gracefully to provider-only metrics (no file-level waste attribution, clearly labeled).

---

## 5. Analysis Layer

### 5.1 Sessionization (D10) — closes G5
- Hash each message after `normalize_volatile` (content hash, excluding volatile hash/timestamps).
- Build sessions by **longest-common-prefix** matching of consecutive requests' hashed message arrays, bounded by a configurable time gap.
- **Compaction/microcompact detection:** a sharp prefix discontinuity coinciding with a summary-shaped leading message is tagged `SESSION_EVENT=compaction` and *continues* the session rather than splitting it.
- **Sub-agent branches:** concurrent divergent prefixes on the same instance are tagged as child branches under the parent session (closes the parallel-subagent gap).

### 5.2 Cross-turn diffing (FR-2.1) — the analytical core
- For each `FileReadEvent`, key by `(path, content_hash)`. A repeat read of an identical `(path, content_hash)` in a later turn within the same session is a **re-read**; attribute the token cost of the repeated payload.
- Redundant scan/glob (FR-2.3): repeated broad directory enumerations with overlapping result sets within a session.

### 5.3 Context-growth curve (FR-2.2)
- Per turn: total input tokens, and the share that is re-sent prior context (prefix-matched bytes), **each costed at its actual billed rate** using the cache breakdown.

### 5.4 Waste attribution (D7) — closes G3/G4; see Appendix C
- **wasted_tokens** = re-read identical content + redundant scans (measured, cache-rate-adjusted).
- **avoidable_pct** = the conservative subset eliminable by a codebase index/cache, as a share of total billed input cost.
- Fixed overhead (FR-2.4): system prompt + tool/skill defs carried every call, quantified separately.
- All three computed in DuckDB over the attached SQLite, never mutating the source.

### 5.5 Ranking (FR-2.6)
- Rank turns by billed cost; attach a heuristic cause label (`re-read`, `context-bloat`, `large-tool-output`, `scan`).

---

## 6. Presentation & Pricing

### 6.1 Pricing (closes G11)
- `pricing/defaults.toml` seeded from LiteLLM's `model_prices_and_context_window` dataset (attribution in NOTICE), user-overridable per FR-4.4. Each price row carries an `as_of` date; reports surface price staleness.

### 6.2 P0 report (D9)
- Jinja2 → one self-contained `report.html`: header waste %, per-turn token bars (inline SVG), top contributors, methodology footnote linking the formal definition. Doubles as FR-3.5 export.

### 6.3 P1 web UI
- React/Vite SPA: session timeline, drill-down (FR-3.3), cost panel (FR-3.4), A/B compare (FR-3.6). Read-only local HTTP API over the store.

### 6.4 Export (D3)
- `agenttrace export --format {md,json,otel}`. The `otel` format maps turns to `gen_ai.*` (system_instructions/input.messages/output.messages + token usage), pinned to a named convention version recorded in `schema_version`.

---

## 7. Phased Delivery (maps PRD §11)

### P0 — Weekend MVP (capture truth, prove the number)
1. Repo scaffold, `pyproject.toml` (PEP 621 `[project]` + Poetry groups, Appendix D), `poetry.lock` committed, ruff/mypy/pytest config, pre-commit, CI skeleton.
2. SQLite schema + migrations (Appendix A); Writer task + WAL + bounded queue.
3. Tee-ing Starlette proxy + httpx streaming pipe; fail-open guard.
4. Anthropic provider adapter + Claude Code agent adapter (incl. volatile-hash strip).
5. Authoritative usage parsing (incl. cache fields); partial-stream handling.
6. Minimal re-read diff + cache-net waste % (Appendix C); static HTML report.
7. `agenttrace start|report` CLI; printed setup snippet.
- **Exit:** a real Claude Code session yields a screenshot — "X% of billed input tokens were spent re-reading unchanged files" — reconciled within tolerance against Anthropic-reported usage.

### P1 — Usable tool
8. OpenAI provider + Aider agent adapter (second target, D5).
9. DuckDB analytics; context-growth curve; turn ranking + cause labels.
10. React/Vite SPA with drill-down + cost panel; local read API.
11. Tokenizer fallback (tiktoken, low-confidence labels); estimator backfill for partial streams.
12. `gen_ai.*` export.
- **Exit:** demoable, useful for the author's daily work.

### P2 — Polish
13. Session A/B compare; redaction-at-rest options (FR-4.3); retention/size caps (closes G10).
14. Packaging ergonomics (pipx-installable wheel; `pipx install agenttrace`), docs, methodology page, install matrix incl. Windows notes (closes G14).
- **Exit:** shareable, star-worthy release.

---

## 8. Testing Strategy

Coverage gate: **≥85% lines, ≥75% branches** on `analysis/`, `adapters/`, `tokens/`, `capture/`. Hot-path proxy covered by integration + load tests rather than line coverage alone.

### 8.1 Unit tests (pytest)
- **Adapters:** golden request/response fixtures (recorded, credential-scrubbed) for Anthropic + Claude Code, OpenAI + Aider. Assert usage parsing incl. cache breakdown; assert volatile-hash stripping is deterministic.
- **Tokenizer:** estimator always emits `confidence="low"`; authoritative path preferred when usage present.
- **Error paths (anti-pattern guard):** every adapter tested for malformed bodies, missing usage, truncated streams — not just happy paths. Specific-exception assertions (`pytest.raises(... match=...)`).

### 8.2 Property-based tests (hypothesis)
- **Sessionizer:** for generated sequences of message arrays with random appends/compactions, assert (a) monotonic prefix property holds, (b) compaction never spuriously splits a session, (c) concurrent branches attach to the right parent.
- **Differ:** identical `(path, content_hash)` reads are always counted as re-reads; changed content is never counted as re-read (no false positives).
- **Waste:** waste_tokens ≤ total billed input tokens for all inputs (invariant); cache-read content never costed at full rate.

### 8.3 Integration tests (respx + ASGI test client)
- Spin the Starlette app against a respx-mocked upstream that emits realistic Anthropic SSE (incl. cache + usage frames) and OpenAI streams.
- Assert byte-exact passthrough (response bytes out == bytes in), correct capture rows written, and **fail-open**: inject capture/DB errors and assert the client response is unaffected.

### 8.4 End-to-end (recorded sessions)
- A library of recorded, scrubbed real agent sessions (Claude Code multi-file task with compaction; Aider edit loop). Replay through the proxy; assert the headline waste % reconciles with provider-reported totals within tolerance (PRD §10.1).

### 8.5 Reconciliation test (the credibility gate)
- Dedicated suite asserting that summed billed cost computed by AgentTrace equals provider-reported usage × price table, within ±1%, across the recorded corpus. Failure here blocks release regardless of other green.

---

## 9. CI/CD — actions on every push & PR

**Platform:** GitHub Actions. Matrix: Python {3.11, 3.12} × OS {ubuntu-latest, macos-latest} (+ windows-latest smoke for CLI start/report).

### 9.1 On every push and pull_request
`ci.yml` jobs (fail-fast off; all must pass to merge). Every job runs `pipx install poetry==2.1.*`, then `poetry install --with dev,test --sync` after `actions/setup-python` with Poetry's venv cached on `poetry.lock` hash:
1. **lockfile-check** — `poetry check --lock` (fails if `pyproject.toml` and `poetry.lock` have drifted).
2. **lint** — `poetry run ruff check .` + `poetry run ruff format --check .`.
3. **types** — `poetry run mypy --strict agenttrace`.
4. **test** — `poetry run pytest -q --cov=agenttrace --cov-branch --cov-report=xml --cov-fail-under=85`.
5. **security** — `poetry run pip-audit` (run against the installed environment); fail on known-vuln dependencies.
6. **secret-scan** — gitleaks; blocks if any credential-shaped string is committed.
7. **build** — `poetry build`; in a clean venv `pip install dist/*.whl` and assert `agenttrace --help` runs.

### 9.2 On pull_request only
8. **reno/changelog check** — PR must touch docs/changelog if it changes public behavior.
9. **mandatory code review** — per the team's review protocol: before merge to `main`, a reviewer evaluates the diff between `BASE_SHA` (origin/main) and `HEAD_SHA` against the task's requirements; Critical issues block, Important issues block, Minor issues are logged. Review is required after each major feature and before every merge — never skipped for "simple" changes.

### 9.3 Branch protection
- `main` protected: linear history, required status checks (jobs 1–7), ≥1 approving review, no force-push.
- **pre-commit** mirrors jobs 2–3 + secret-scan locally (`poetry run` hooks) so failures surface before push.

### 9.4 On tag (release)
`release.yml`: `poetry build` (sdist+wheel), `poetry run twine check dist/*`, publish to PyPI via OIDC trusted publishing (no long-lived token), attach the SBOM and a signed checksum. Version is read from `[project].version`; tag must match.

---

## 10. Stress-Testing Strategy (soundness of the hot path)

The proxy is on the agent's critical path; its failure modes are the project's reputational risk. The regime below proves the NFRs.

### 10.1 Latency budget verification (NFR <10 ms p95)
- **Harness:** a mock upstream (respx or a local echo server) with controllable TTFB and inter-token delay, fed by `k6`/`locust`. Measure *added* latency = (proxied path) − (direct-to-mock path), isolating model time.
- **Assertions:** added p95 < 10 ms, added p99 < 25 ms, **TTFB delta < 5 ms** (streaming feel preserved). Regression budget enforced in a nightly perf job; a >20% regression fails.

### 10.2 Streaming integrity under adversity
- Inject mid-stream upstream disconnects, slow-loris clients, and client cancellations. Assert: no event-loop stalls, capture marks `partial`, agent never receives corrupted SSE framing, no orphaned upstream connections (connection-pool leak check).

### 10.3 Throughput & backpressure
- Drive concurrent sessions (e.g., 50–200 simultaneous streams, simulating parallel sub-agents). Assert the bounded capture queue degrades by *dropping-and-counting* (never blocking the hot path), and that drops are surfaced as a metric, not silent loss.

### 10.4 Write-path contention (D8)
- Sustained high-rate capture into SQLite WAL via the single Writer. Assert zero "database is locked" errors, bounded WAL growth (checkpoint cadence), and commit-batch latency within target. DuckDB analytics run concurrently (read-only attach) must not block writes.

### 10.5 Data-volume soak (closes G10)
- Replay a synthetic million-input-token session with full bodies. Measure DB growth, report-render time, and DuckDB query latency on the largest realistic store. Validates retention/size-cap defaults set in P2.

### 10.6 Fault injection / fail-open proof
- Chaos suite: randomly raise in the parser, fill the disk, kill the Writer task. Invariant under all: **the agent's request still completes correctly.** This is the single most important stress assertion — a tracer that can break the agent is worse than no tracer.

### 10.7 Correctness-under-load
- Combine 10.1–10.4 with the reconciliation test (8.5): under load, the waste number must still reconcile with provider usage within tolerance. Proves the metric is not a load-dependent artifact.

---

## 11. Risk Register (live)

| ID | Risk | Mitigation in this plan |
|----|------|-------------------------|
| R-CACHE-HASH | Claude Code per-request system-prompt hash + microcompact corrupt prefix-match & waste | `normalize_volatile` strips hash before hashing; compaction is a tagged session event (§4.2, §5.1) |
| R-CACHE-COST | Counting cached re-sent context at full price overstates waste | Cache-net definition; costs use `cache_read_input_tokens` rate (§5.4, App. C) |
| R-LATENCY | Capture work leaks onto hot path | Tee-only hot path; all semantics in Writer/Analysis; perf gate §10.1 |
| R-LOCK | SQLite concurrent-write errors | Single-writer queue + WAL; soak test §10.4 |
| R-INTERCEPT | Some agents don't honor base-URL override | v1 scoped to verified Claude Code + Aider; Cursor deferred (D5/G12) |
| R-TOKENIZER | No local Anthropic tokenizer | Authoritative usage primary; tiktoken fallback labeled low-confidence; `count_tokens` API forbidden (D6/G7) |
| R-KEYLEAK | Proxy holds real API key | In-memory only, never persisted/logged; secret-scan + redaction (§3.1/D11) |
| R-SCHEMA | OTel GenAI conventions still in Develop | `gen_ai.*` is export-only, version-pinned; internal store provider-verbatim (D3/D13) |
| R-FAILOPEN | A tracer bug breaks the agent | Fail-open guard + chaos suite §10.6 (release-blocking) |

---

## Appendix A — SQLite schema (DDL sketch)

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE schema_meta (
  schema_version TEXT NOT NULL,
  otel_genai_convention TEXT,        -- pinned version string, nullable in P0
  created_at TEXT NOT NULL
);

CREATE TABLE requests (
  id INTEGER PRIMARY KEY,
  recv_ts TEXT NOT NULL,
  conn_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,            -- claude_code | aider | unknown
  provider TEXT NOT NULL,            -- anthropic | openai
  model TEXT,
  system_prompt_hash TEXT,           -- post volatile-normalization
  message_hashes_json TEXT,          -- ordered list of per-message content hashes
  tool_defs_hash TEXT,
  body_blob BLOB,                    -- optional; droppable via FR-4.3 redaction
  usage_input INTEGER, usage_output INTEGER,
  cache_read_input INTEGER, cache_creation_input INTEGER,
  usage_source TEXT,                 -- reported | partial | estimated
  estimate_confidence TEXT           -- null | low
);

CREATE TABLE file_read_events (
  id INTEGER PRIMARY KEY,
  request_id INTEGER NOT NULL REFERENCES requests(id),
  path TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  approx_tokens INTEGER
);

CREATE TABLE session_events (
  id INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL,
  request_id INTEGER NOT NULL REFERENCES requests(id),
  event_type TEXT,                   -- start | continue | compaction | branch
  parent_session_id TEXT
);

CREATE INDEX idx_req_conn ON requests(conn_id, recv_ts);
CREATE INDEX idx_fre_hash ON file_read_events(path, content_hash);
```
Sessions and turns are derived in DuckDB; only `session_events` materializes the reconstruction decisions for auditability.

## Appendix B — Config schema (pydantic-settings, env-driven)

```
AGENTTRACE_PORT (int, default 8788)
AGENTTRACE_UPSTREAM_ANTHROPIC (url, default https://api.anthropic.com)
AGENTTRACE_UPSTREAM_OPENAI (url, default https://api.openai.com)
AGENTTRACE_DB_PATH (path, default ~/.agenttrace/store.db)
AGENTTRACE_STORE_BODIES (bool, default true)   # FR-4.3 redaction toggle
AGENTTRACE_RETENTION_DAYS (int, default 30)    # G10
AGENTTRACE_QUEUE_MAXSIZE (int, default 10000)
AGENTTRACE_PRICE_TABLE (path, default bundled defaults.toml)
```
No credentials appear in config; the proxy reads them only from forwarded request headers, in memory.

## Appendix C — Formal "waste %" definition (the credibility metric, D7)

Let, per session, over all billed requests:
- `B_input` = total billed input cost = Σ ( (input_tokens − cache_read_input) × price_input + cache_read_input × price_cache_read + cache_creation_input × price_cache_write ).
- `W_reread` = Σ over re-read file payloads of (payload_tokens × applicable billed rate), where "applicable rate" is the cache-read rate when that payload was served from cache, else full input rate.
- `W_scan` = analogous for redundant scan/glob result payloads.

Then:
- **wasted_tokens / wasted_cost** = `W_reread + W_scan` (measured, cache-adjusted).
- **avoidable_pct** = `(W_reread + W_scan) / B_input`, reported as the conservative lower bound (only content provably identical and re-sent counts).
- **fixed_overhead** (FR-2.4) = Σ (system_prompt + tool_defs tokens) × billed rate, reported separately, *not* folded into waste.

Methodology is published verbatim in the report footer and docs; conservatism is preferred over a larger, less defensible number (PRD §10.2).

---

## Appendix D — `pyproject.toml` skeleton (Poetry 2.x, PEP 621)

Copy-paste and pin exact versions in `poetry.lock` via `poetry lock`. Floors shown; resolver picks compatible exacts.

```toml
[project]
name = "agenttrace"
version = "0.1.0"
description = "Local token-waste profiler for AI coding agents"
requires-python = ">=3.11,<3.14"
license = "Apache-2.0"
authors = [{ name = "Vigi" }]
dependencies = [
    "starlette>=1.0,<2",
    "uvicorn>=0.34",
    "httpx[http2]>=0.28",
    "duckdb>=1.5,<2",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "typer>=0.12",
    "structlog>=24.1",
    "tiktoken>=0.7",
    "jinja2>=3.1",
]

[project.scripts]
agenttrace = "agenttrace.cli:app"

[tool.poetry.group.dev.dependencies]
ruff = ">=0.6"
mypy = ">=1.10"
pre-commit = ">=3.7"
pip-audit = ">=2.7"

[tool.poetry.group.test.dependencies]
pytest = ">=8.2"
pytest-asyncio = ">=0.23"
pytest-cov = ">=5.0"
respx = ">=0.21"
hypothesis = ">=6.100"

[tool.poetry]
packages = [{ include = "agenttrace" }]
requires-poetry = ">=2.1"

[build-system]
requires = ["poetry-core>=2.0"]
build-backend = "poetry.core.masonry.api"

[tool.ruff]
line-length = 100
target-version = "py311"
[tool.ruff.lint]
select = ["E", "F", "I", "B", "ASYNC", "UP", "SIM", "TID"]

[tool.mypy]
strict = true
python_version = "3.11"
warn_unreachable = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
addopts = "-ra"
```

SQLite uses the stdlib `sqlite3` module (no dependency). The React/Vite SPA (P1) lives in a separate `webui/` workspace with its own `package.json`, not in `pyproject.toml`.

---

## Appendix E — Implementation-Determinism Conventions (no-assumptions contract)

This appendix exists so a Sonnet-class implementer can build each component **without inventing any value or algorithm**. Where the body of the plan said "a hash" or "a heuristic," the exact rule is fixed here. If something is genuinely a choice, a default is mandated; deviation requires a code comment citing this appendix.

### E.1 Canonical hashing (used by sessionizer, differ, store)
- **Algorithm:** SHA-256, hex digest, of UTF-8 bytes. No other hash anywhere.
- **JSON canonicalization before hashing:** `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`. Always canonicalize before hashing any structured value.
- **Message content hash:** hash the canonicalized content blocks of a single message *after* `normalize_volatile` (E.4), excluding any `cache_control` annotations and excluding server-added fields.
- **File content hash:** SHA-256 of the raw file text exactly as it appeared in the tool result (no trimming, no newline normalization).

### E.2 HTTP forwarding rules (the proxy hot path)
- **Forward verbatim:** method, path, query, and the request body bytes unchanged.
- **Forward these headers unchanged (critical):** `authorization`, `x-api-key`, `anthropic-version`, `anthropic-beta`, `content-type`, and any `x-*` header. Beta headers MUST be preserved or provider behavior changes.
- **Strip/recompute hop-by-hop headers:** drop `connection`, `keep-alive`, `proxy-authenticate`, `proxy-authorization`, `te`, `trailer`, `transfer-encoding`, `upgrade`. Recompute/let httpx set `host` and `content-length`. Never set `accept-encoding` to something the client didn't send (avoid decompressing surprises).
- **Upstream selection:** route by detected provider (E.3) to `AGENTTRACE_UPSTREAM_ANTHROPIC` / `_OPENAI`. No path rewriting beyond joining base URL + original path.
- **Timeouts (centralized, single httpx client):** connect 10 s, read `None` (streaming may be long), write 30 s, pool 10 s. No per-call retry in the proxy (agents retry themselves — avoids double-retry anti-pattern).

### E.3 Provider/agent detection (exact order)
1. **Provider:** if request path contains `/v1/messages` OR `anthropic-version` header present → `anthropic`; elif path contains `/v1/chat/completions` OR `/v1/responses` → `openai`; else `unknown` (still forwarded; capture marked `provider=unknown`, no semantic analysis).
2. **Agent:** read `user-agent` and known marker headers. Claude Code sends a `user-agent` beginning `claude-cli/`; Aider sets an identifiable UA / `x-title`. If no match → `agent=unknown` (provider-level metrics only). The exact UA substrings are stored in `adapters/agents/markers.toml` and matched case-insensitively; if a marker is unknown at runtime, classify `unknown`, never guess.

### E.4 `normalize_volatile` (Claude Code, R-CACHE-HASH) — exact procedure
- Claude Code injects a per-request varying token into the **system prompt** (and may microcompact history). To make message hashing stable:
  1. Take the system prompt blocks. For each text block, apply a fixed regex that removes a trailing/embedded volatile token of the form matching `[0-9a-f]{8,}` that sits alone on its own line or after a known marker; the exact regex lives in `adapters/agents/claude_code.py` as `VOLATILE_HASH_RE` with an inline test fixture proving it strips the real value and nothing else.
  2. If the regex matches **zero or more than one** site, do **not** strip (log `volatile_strip_ambiguous` metric and hash as-is) — never silently remove content you can't pin. This keeps the differ conservative.
- Microcompact/compaction is **not** stripped; it is detected in E.5 and recorded as a session event.

### E.5 Sessionization parameters (exact)
- **Time-gap bound:** a request joins the current session if `recv_ts - prev_recv_ts <= 1800 s` (30 min) on the same `agent_id`; otherwise start a new session. Configurable via `AGENTTRACE_SESSION_GAP_SECONDS` (default 1800).
- **Prefix continuity:** request *R* continues session *S* if the hashed-message array of *S*'s last request is a **prefix** of *R*'s hashed-message array. Tag `continue`.
- **Compaction:** if prefix continuity fails BUT (a) time-gap holds and (b) *R*'s leading message is summary-shaped (single large assistant/system message replacing ≥3 prior messages, detected by: first message token estimate ≥ 60% of the dropped span's estimate) → tag `compaction`, keep the same `session_id`.
- **Branch (sub-agent):** if two requests within the gap share a common prefix but then diverge concurrently (overlapping in time on different `conn_id`) → the later root is tagged `branch` with `parent_session_id` = the shared-prefix session.
- **Turn grouping:** consecutive requests in a session where each is caused by a tool-use loop (response contained `tool_use`/`tool_calls` and the next request appends the matching `tool_result`/`tool` message) collapse into one **turn**. Correlate by `tool_use_id` (Anthropic) / `tool_call_id` (OpenAI).

### E.6 File-read extraction & re-read attribution (exact join)
- In a `CanonicalRequest`, find assistant `tool_use` blocks whose tool name is classified `FILE_READ` by the agent adapter (Claude Code: `Read`, `view`; Aider: file-context add / `cat`). Record the requested `path` and the `tool_use_id`.
- Find the corresponding `tool_result` block (same `tool_use_id`) in the *next* request's messages; its content is the file payload. Compute `content_hash` (E.1) and `approx_tokens` = tiktoken count of the payload (confidence `low`, since it's an estimate of a sub-portion).
- **Re-read:** a `(path, content_hash)` that already appeared as a `FILE_READ` payload earlier in the same session. Its `approx_tokens` × applicable billed rate (E.7) accrues to `W_reread`.
- **Not a re-read:** same path, different `content_hash` (file changed) — never counted.

### E.7 Billed-rate selection (per token category)
- For a request with provider-reported usage, the **authoritative totals** are used for the headline reconciliation (§8.5). The per-payload attribution uses **estimated** token counts apportioned at the request's effective rates:
  - cache-read tokens → `price_cache_read` (Anthropic default 0.1× input; from price table, never hard-coded).
  - cache-creation tokens → `price_cache_write` (default 1.25× input).
  - remaining input → `price_input`.
- If a request lacks cache fields (e.g., OpenAI without cached_tokens), treat all input as full-rate input.
- **Reconciliation exclusion:** turns whose `usage_source` is `partial` or `estimated` are excluded from the ±1% reconciliation gate and clearly flagged in the report; they may still appear in waste estimates, labeled low-confidence.

### E.8 Store, migrations, retention (exact)
- **DB path:** `AGENTTRACE_DB_PATH`, default `~/.agenttrace/store.db`; `~` expanded via `pathlib.Path.expanduser()`; parent dir created mode `0700`.
- **Open pragmas (every connection):** `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;`.
- **Writer commit batching:** flush on 200 events OR 500 ms, whichever first.
- **Queue overflow:** bounded at `AGENTTRACE_QUEUE_MAXSIZE` (default 10000); on full, drop newest and increment `capture.dropped` (never block hot path).
- **Migrations:** numbered SQL files `migrations/0001_init.sql`, `0002_*.sql`, …; on startup compare max applied (from `schema_meta.schema_version`) and apply missing in order inside a transaction. No ORM, no autogen.
- **DuckDB attach (read-only):** `INSTALL sqlite; LOAD sqlite; ATTACH '<db_path>' AS s (TYPE sqlite, READ_ONLY);` — analysis queries read from `s.*`, never write.
- **Retention (P2):** delete requests older than `AGENTTRACE_RETENTION_DAYS` (default 30) on `agenttrace start`; cascade to child tables.

### E.9 Pricing table format (exact, `pricing/defaults.toml`)
```toml
[[model]]
id = "claude-opus-4-8"
provider = "anthropic"
price_input = 0.000015        # USD per token
price_output = 0.000075
price_cache_read = 0.0000015
price_cache_write = 0.00001875
as_of = "2026-05-01"
```
Loader validates with a Pydantic model; unknown model → cost shown as `null` with a "no price" badge (never a guessed price). Seed values are attributed to the LiteLLM `model_prices_and_context_window` dataset in `NOTICE`.

### E.10 CLI surface (exact)
- `agenttrace start [--port 8788] [--db PATH]` — runs proxy; prints per-agent setup snippets then blocks.
- `agenttrace report [--session LATEST|<id>] [--out report.html]` — runs analysis synchronously over the store via DuckDB; writes the static HTML report (P0) and prints the headline waste %.
- `agenttrace export --session <id> --format {md,json,otel} [--out PATH]`.
- `agenttrace compare --a <id> --b <id>` (P2).
- Session id format: `s_<recv_ts_compact>_<6hex>`; `LATEST` resolves to the most recent `session_id`.

### E.11 Acceptance criteria per P0 task (definition of done)
Each P0 task is "done" only when: (a) public functions are fully type-hinted and pass `mypy --strict`; (b) error paths *and* happy paths have tests; (c) no bare `except`, no blocking calls in async, resources via context managers (python-anti-patterns checklist passes); (d) for capture/proxy tasks, an integration test proves byte-exact passthrough AND fail-open under an injected capture error; (e) a code review against `BASE_SHA..HEAD_SHA` has been completed with no unresolved Critical/Important issues.

---

*End of Master Implementation Plan. Ready to begin P0 scaffolding on your go.*