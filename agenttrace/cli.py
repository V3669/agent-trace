"""AgentTrace CLI entry point.

Commands
--------
agenttrace start    — run capture proxy (P0+)
agenttrace report   — render static HTML waste report (P0+)
agenttrace serve    — serve React SPA + read-only API (P1+)
agenttrace export   — export session data (md / json / otel) (P0+; otel P1+)
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Annotated

import structlog
import typer
import uvicorn

from agenttrace.capture.db import apply_migrations, open_db
from agenttrace.config import settings
from agenttrace.models import CaptureEvent

app = typer.Typer(name="agenttrace", no_args_is_help=True)
logger = structlog.get_logger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


def _setup_db(db_path: Path) -> sqlite3.Connection:
    conn = open_db(db_path)
    apply_migrations(conn, _MIGRATIONS_DIR)
    return conn


def _print_setup_snippets(port: int) -> None:
    typer.echo(f"\nAgentTrace proxy running on http://127.0.0.1:{port}\n")
    typer.echo("── Claude Code ──────────────────────────────────────────")
    typer.echo(f"  export ANTHROPIC_BASE_URL=http://127.0.0.1:{port}")
    typer.echo("  # Then run Claude Code as normal\n")
    typer.echo("── Aider ────────────────────────────────────────────────")
    typer.echo(f"  aider --openai-api-base http://127.0.0.1:{port}/v1")
    typer.echo(f"  # Or: export OPENAI_API_BASE=http://127.0.0.1:{port}/v1\n")
    typer.echo("Press Ctrl+C to stop.\n")


@app.command()
def start(
    port: Annotated[int, typer.Option("--port", "-p")] = settings.port,
    db: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """Start the capture proxy (forwards traffic + records to DB)."""
    db_path = db or settings.db_path
    conn = _setup_db(db_path)
    capture_queue: asyncio.Queue[CaptureEvent] = asyncio.Queue(maxsize=settings.queue_maxsize)

    from agenttrace.capture.writer import writer_task
    from agenttrace.proxy.app import create_app

    starlette_app = create_app(capture_queue)

    async def run() -> None:
        writer = asyncio.create_task(writer_task(capture_queue, conn))
        config = uvicorn.Config(starlette_app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        _print_setup_snippets(port)
        try:
            await server.serve()
        finally:
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
            conn.close()

    asyncio.run(run())


@app.command()
def report(
    session: Annotated[str, typer.Option("--session")] = "LATEST",
    out: Annotated[Path, typer.Option("--out")] = Path("report.html"),
    db: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """Render a static HTML waste report for a session."""
    db_path = db or settings.db_path
    if not db_path.exists():
        typer.echo(f"No database at {db_path}. Run `agenttrace start` first.", err=True)
        raise typer.Exit(1)

    from agenttrace.analysis.waste import compute_waste
    from agenttrace.report.renderer import render_html_report

    session_id = _resolve_session(session, db_path)
    waste = compute_waste(db_path, session_id)
    render_html_report(waste, out)
    typer.echo(f"Report: {out}")
    pct = waste.avoidable_pct
    typer.echo(f"  {pct:.1f}% of billed input tokens were spent re-reading unchanged files")
    typer.echo(f"  Total billed input cost: ${waste.total_billed_input_cost:.4f}")
    typer.echo(f"  Wasted cost:             ${waste.wasted_cost:.4f}")


@app.command()
def serve(
    port: Annotated[int, typer.Option("--port", "-p")] = 8789,
    db: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """Serve the React SPA and read-only analytics API.

    The SPA is served from agenttrace/webui/dist/ (build with `npm run build`
    inside the webui/ directory first).  The API is available at /api/* even
    without a built SPA.
    """
    db_path = db or settings.db_path
    if not db_path.exists():
        typer.echo(
            f"No database at {db_path}. Run `agenttrace start` first to capture sessions.",
            err=True,
        )
        raise typer.Exit(1)

    from agenttrace.webui.api import create_api_app

    web_app = create_api_app(db_path)

    typer.echo(f"\nAgentTrace UI → http://127.0.0.1:{port}")
    typer.echo(f"API            → http://127.0.0.1:{port}/api/sessions")
    typer.echo("Press Ctrl+C to stop.\n")

    uvicorn.run(web_app, host="127.0.0.1", port=port, log_level="warning")


@app.command()
def export(
    session: Annotated[str, typer.Option("--session")] = "LATEST",
    format: Annotated[str, typer.Option("--format")] = "json",
    out: Annotated[Path | None, typer.Option("--out")] = None,
    db: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """Export session data in the requested format (json | md | otel)."""
    db_path = db or settings.db_path
    if not db_path.exists():
        typer.echo(f"No database at {db_path}.", err=True)
        raise typer.Exit(1)

    from agenttrace.analysis.waste import compute_waste
    from agenttrace.report.exporter import export_json, export_markdown, export_otel

    session_id = _resolve_session(session, db_path)
    output_path = out or Path(f"agenttrace_{session_id}.{format}")

    if format == "json":
        waste = compute_waste(db_path, session_id)
        export_json(waste, output_path)
    elif format == "md":
        waste = compute_waste(db_path, session_id)
        export_markdown(waste, output_path)
    elif format == "otel":
        export_otel(db_path, session_id, output_path)
    else:
        typer.echo(f"Unknown format: {format!r}. Choose from: json, md, otel.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Exported to {output_path}")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _resolve_session(session: str, db_path: Path) -> str:
    """Resolve 'LATEST' to the actual session_id; validate non-empty."""
    from agenttrace.analysis.waste import get_latest_session_id

    if session == "LATEST":
        sid = get_latest_session_id(db_path)
        if not sid:
            typer.echo("No sessions found in database.", err=True)
            raise typer.Exit(1)
        return sid
    return session


if __name__ == "__main__":
    app()
