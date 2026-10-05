from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from tf_backend.api import characters, health, media, providers, runs, settings, trends
from tf_backend.app_context import AppContext
from tf_backend.services import Services

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "Frontend" / "dist"


def create_app(services: Services | None = None, context: AppContext | None = None) -> FastAPI:
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
        await ctx.runs.resume_unfinished()  # a restart never loses a run (Plan 4 review focus 3)
        try:
            yield
        finally:
            await cleanup()

    app = FastAPI(title="Trend Finder", version="0.1.0", lifespan=lifespan)
    if services is not None:
        app.state.services = services
    if context is not None:
        app.state.context = context
    for module in (health, providers, characters, runs, trends, settings, media):
        app.include_router(module.router, prefix="/api")
    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
    return app


app = create_app()
