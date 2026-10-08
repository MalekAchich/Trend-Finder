import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse

from tf_backend.api import (accounts, characters, download, health, manual, media, preview, providers, runs, settings,
                            socials, videos)
from tf_backend.app_context import AppContext
from tf_backend.services import Services

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "Frontend" / "dist"
LOCAL_HOSTS = ["127.0.0.1", "localhost", "[::1]"]


def create_app(services: Services | None = None, context: AppContext | None = None,
               frontend_dist: Path | None = FRONTEND_DIST) -> FastAPI:
    injected = services is not None or context is not None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if injected:
            yield
            return
        from tf_backend.context_builder import build_context

        ctx, sv, cleanup = await build_context()
        app.state.context, app.state.services = ctx, sv
        await sv.registry.refresh()
        from tf_backend.model_settings import load_model_choices

        await load_model_choices(ctx.sessionmaker, sv)
        await ctx.runs.resume_unfinished()  # a restart never loses a run (Plan 4 review focus 3)
        try:
            yield
        finally:
            await cleanup()

    app = FastAPI(title="Trend Finder", version="0.1.0", lifespan=lifespan)
    # No login: a DNS-rebinding page could otherwise reach 127.0.0.1 under its own name. `tf serve --allow-remote`
    # sets TF_ALLOWED_HOSTS=*.
    hosts = [h.strip() for h in os.environ.get("TF_ALLOWED_HOSTS", "").split(",") if h.strip()] or LOCAL_HOSTS
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    if services is not None:
        app.state.services = services
    if context is not None:
        app.state.context = context
    for module in (health, providers, characters, runs, videos, manual, preview, download, settings, accounts,
                   socials, media):
        app.include_router(module.router, prefix="/api")
    if frontend_dist is not None and frontend_dist.is_dir():
        _serve_web_app(app, frontend_dist.resolve())
    return app


def _serve_web_app(app: FastAPI, dist: Path) -> None:
    """Built files as-is; any other non-API path is a client-side route, so it gets index.html."""
    index = dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def web_app(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Not Found")
        file = (dist / path).resolve()
        if path and file.is_relative_to(dist) and file.is_file():
            return FileResponse(file, headers={"Cache-Control": "public, max-age=31536000, immutable"}
                                if file.parent.name == "assets" else None)
        if "." in path.rsplit("/", 1)[-1]:  # a missing file, not a page
            raise HTTPException(404, "Not Found")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


app = create_app()
