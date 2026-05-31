# AgentTrace

> *How much of what you're paying for is Claude re-reading files it already read?*

AgentTrace is a transparent proxy that sits between your AI coding agent (Claude Code, Aider) and the provider API. It captures every request, identifies re-read file content and redundant scans, then tells you exactly what percentage of your billed input tokens were wasted re-reading unchanged files.

```
  Claude Code ──▶ AgentTrace proxy ──▶ api.anthropic.com
                       │
                  (captures tee)
                       │
                  SQLite store ──▶ DuckDB analysis ──▶ report.html

  "34.2% of billed input tokens were spent re-reading unchanged files"
```

---

## How it works

1. You point your agent at the proxy instead of the API directly — one env var change, nothing else.
2. The proxy forwards every request byte-for-byte with zero added latency (< 10ms p95 overhead). It tees a copy of each request/response onto an out-of-band capture queue.
3. After your session, run `agenttrace report` to get a single self-contained HTML file: headline waste %, per-file re-read breakdown, and billed cost attribution.

---

## Install

```bash
pipx install agenttrace
```

Or if you're hacking on it:

```bash
git clone https://github.com/vighneshq/agent-trace
cd agent-trace
poetry install --with dev,test
```

---

## Quick start

**Start the proxy:**
```bash
agenttrace start
```

**Point Claude Code at it** (in your shell before running `claude`):
```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8788
```

**Do your normal Claude Code session.** When done, hit Ctrl+C, then:

```bash
agenttrace report
# → report.html  (open in browser)
# → 34.2% of billed input tokens were spent re-reading unchanged files
```

---

## Commands

```
agenttrace start [--port 8788] [--db PATH]     # run proxy, blocks
agenttrace report [--session LATEST] [--out report.html]
agenttrace export --session LATEST --format json
```

---

## What it measures

AgentTrace tracks **re-read waste**: when the agent reads the same file (same path, same content hash) more than once in a session, every repeat read is waste. Each wasted token is costed at the cache-read rate (Anthropic's 0.1× input rate) — the conservative lower bound of what you actually paid.

**Waste % formula:**

```
avoidable_pct = (re-read tokens × cache-read rate) / total billed input cost × 100
```

Full methodology is printed in the report footer.

---

## Configuration

All config via environment variables, no config files needed:

| Var | Default | Purpose |
|-----|---------|---------|
| `AGENTTRACE_PORT` | `8788` | Proxy listen port |
| `AGENTTRACE_DB_PATH` | `~/.agenttrace/store.db` | SQLite capture store |
| `AGENTTRACE_UPSTREAM_ANTHROPIC` | `https://api.anthropic.com` | Override upstream |
| `AGENTTRACE_STORE_BODIES` | `true` | Persist request bodies (for replay) |
| `AGENTTRACE_QUEUE_MAXSIZE` | `10000` | Capture queue depth |
| `AGENTTRACE_SESSION_GAP_SECONDS` | `1800` | Idle gap that starts a new session |

Your API keys are never stored — the proxy reads them from forwarded headers and keeps them in memory only.

---

## Architecture notes (if you're curious)

The proxy has a hard hot-path invariant: the forwarding path does **nothing** except read bytes and pipe them upstream. Zero parsing, zero DB I/O on the hot path. All analysis happens off-path in a Writer task and on-demand at report time.

```
proxy handler  →  httpx stream  →  provider
      ↓
  capture buf  →  asyncio.Queue  →  Writer task  →  SQLite (WAL)
                                                        ↓
                                              DuckDB (read-only attach)
                                                        ↓
                                              Jinja2 → report.html
```

Fail-open: any capture error is caught and logged. Your agent session is never affected by AgentTrace failures.

---

## Dev

```bash
poetry run pytest                             # 63 tests, ~6s
poetry run pytest --cov=agenttrace --cov-branch --cov-fail-under=85
poetry run ruff check . && ruff format --check .
poetry run mypy --strict agenttrace
```

---

## Status

**P0 (current):** Anthropic/Claude Code capture + static HTML report. Works today.

**P1 (next):** OpenAI + Aider support, DuckDB analytics, React SPA with session drill-down.

**P2:** Session A/B compare, `pipx`-installable wheel, docs.

---

## License

Apache-2.0
