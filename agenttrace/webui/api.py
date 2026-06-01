"""Read-only HTTP API for the React SPA (P1, task 10).

All routes are GET-only and read from the SQLite store via DuckDB.
No writes occur here; the SQLite store is always opened read-only through
DuckDB, matching D8.

Endpoints
---------
GET /api/sessions
    List all sessions, newest first.

GET /api/sessions/{session_id}
    Full waste report for a session.

GET /api/sessions/{session_id}/turns
    Ranked turn list with cause labels (limit=50 by default).

GET /api/sessions/{session_id}/growth
    Context-growth curve (one data point per request).

GET /api/health
    Simple liveness check.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import structlog
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Mount, Route
from starlette.staticfiles import StaticFiles

from agenttrace.analysis.context_growth import compute_context_growth
from agenttrace.analysis.turn_ranker import rank_turns
from agenttrace.analysis.waste import compute_waste, get_latest_session_id, list_sessions

logger = structlog.get_logger(__name__)

_WEBUI_DIST = Path(__file__).parent / "dist"


def create_api_app(db_path: Path) -> Starlette:
    """Return a Starlette app serving the read-only API and SPA static files.

    If the SPA has been built (webui/dist exists), its static files are served
    under ``/`` with the API under ``/api/``.  If the dist directory is absent,
    only the API is available and requests for ``/`` return a 404 with a helpful
    message.

    Args:
        db_path: Absolute path to the SQLite store.
    """

    async def health(_request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def sessions_list(request: Request) -> Response:  # noqa: ARG001
        try:
            summaries = list_sessions(db_path)
            return JSONResponse([s.model_dump() for s in summaries])
        except Exception:
            logger.exception("api.sessions_list.error")
            return JSONResponse({"error": "internal error"}, status_code=500)

    async def session_detail(request: Request) -> Response:
        session_id = request.path_params["session_id"]
        try:
            waste = compute_waste(db_path, session_id)
            return JSONResponse(waste.model_dump())
        except Exception:
            logger.exception("api.session_detail.error", session_id=session_id)
            return JSONResponse({"error": "internal error"}, status_code=500)

    async def session_turns(request: Request) -> Response:
        session_id = request.path_params["session_id"]
        try:
            limit_str = request.query_params.get("limit", "50")
            limit = max(1, min(int(limit_str), 500))
        except ValueError:
            limit = 50
        try:
            turns = rank_turns(db_path, session_id, limit=limit)
            return JSONResponse([asdict(t) for t in turns])
        except Exception:
            logger.exception("api.session_turns.error", session_id=session_id)
            return JSONResponse({"error": "internal error"}, status_code=500)

    async def session_growth(request: Request) -> Response:
        session_id = request.path_params["session_id"]
        try:
            growth = compute_context_growth(db_path, session_id)
            return JSONResponse([asdict(g) for g in growth])
        except Exception:
            logger.exception("api.session_growth.error", session_id=session_id)
            return JSONResponse({"error": "internal error"}, status_code=500)

    async def latest_session(_request: Request) -> Response:
        try:
            sid = get_latest_session_id(db_path)
            if sid is None:
                return JSONResponse({"session_id": None})
            return JSONResponse({"session_id": sid})
        except Exception:
            logger.exception("api.latest_session.error")
            return JSONResponse({"error": "internal error"}, status_code=500)

    api_routes = [
        Route("/api/health", health),
        Route("/api/sessions/latest", latest_session),
        Route("/api/sessions", sessions_list),
        Route("/api/sessions/{session_id}", session_detail),
        Route("/api/sessions/{session_id}/turns", session_turns),
        Route("/api/sessions/{session_id}/growth", session_growth),
    ]

    # Mount static SPA if built; otherwise API-only.
    routes: list[BaseRoute] = list(api_routes)
    if _WEBUI_DIST.exists():
        routes.append(Mount("/", StaticFiles(directory=str(_WEBUI_DIST), html=True)))

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_methods=["GET"],
            allow_headers=["*"],
        )
    ]

    return Starlette(routes=routes, middleware=middleware)
