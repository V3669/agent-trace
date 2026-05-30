from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Annotated

import structlog
import typer
import uvicorn

from agenttrace.capture.db import apply_migrations, open_db
from agenttrace.capture.writer import writer_task
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
    typer.echo("  # Or: export OPENAI_API_BASE=http://127.0.0.1:{port}/v1\n")
    typer.echo("Press Ctrl+C to stop.\n")


@app.command()
def start(
    port: Annotated[int, typer.Option("--port", "-p")] = settings.port,
    db: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """Start the capture proxy."""
    db_path = db or settings.db_path
    conn = _setup_db(db_path)
    capture_queue: asyncio.Queue[CaptureEvent] = asyncio.Queue(maxsize=settings.queue_maxsize)

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
    """Render a static HTML waste report."""
    db_path = db or settings.db_path
    if not db_path.exists():
        typer.echo(f"No database at {db_path}. Run `agenttrace start` first.", err=True)
        raise typer.Exit(1)

    from agenttrace.analysis.waste import compute_waste, get_latest_session_id
    from agenttrace.report.renderer import render_html_report

    session_id = session
    if session == "LATEST":
        session_id = get_latest_session_id(db_path) or ""
        if not session_id:
            typer.echo("No sessions found in database.", err=True)
            raise typer.Exit(1)

    waste = compute_waste(db_path, session_id)
    render_html_report(waste, out)
    typer.echo(f"Report: {out}")
    pct = waste.avoidable_pct
    typer.echo(f"  {pct:.1f}% of billed input tokens were spent re-reading unchanged files")
    typer.echo(f"  Total billed input cost: ${waste.total_billed_input_cost:.4f}")
    typer.echo(f"  Wasted cost:             ${waste.wasted_cost:.4f}")


@app.command()
def export(
    session: Annotated[str, typer.Option("--session")] = "LATEST",
    format: Annotated[str, typer.Option("--format")] = "json",
    out: Annotated[Path | None, typer.Option("--out")] = None,
    db: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """Export session data (md, json)."""
    db_path = db or settings.db_path
    if not db_path.exists():
        typer.echo(f"No database at {db_path}.", err=True)
        raise typer.Exit(1)

    from agenttrace.analysis.waste import compute_waste, get_latest_session_id

    session_id = session
    if session == "LATEST":
        session_id = get_latest_session_id(db_path) or ""
        if not session_id:
            typer.echo("No sessions found.", err=True)
            raise typer.Exit(1)

    waste = compute_waste(db_path, session_id)
    output_path = out or Path(f"agenttrace_{session_id}.{format}")

    if format == "json":
        output_path.write_text(waste.model_dump_json(indent=2), encoding="utf-8")
    elif format == "md":
        lines = [
            f"# AgentTrace Export — {session_id}",
            "",
            f"- **Avoidable waste:** {waste.avoidable_pct:.1f}%",
            f"- **Total billed input cost:** ${waste.total_billed_input_cost:.4f}",
            f"- **Wasted cost:** ${waste.wasted_cost:.4f}",
            f"- **Total requests:** {waste.total_requests}",
            f"- **Re-read files:** {waste.wasted_requests}",
        ]
        output_path.write_text("\n".join(lines), encoding="utf-8")
    else:
        typer.echo(f"Unknown format: {format}. Use json or md.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Exported to {output_path}")


if __name__ == "__main__":
    app()
