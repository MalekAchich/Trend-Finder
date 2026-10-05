from contextlib import asynccontextmanager

from fastapi import FastAPI

from tf_backend.api import health, providers
from tf_backend.services import Services, build_services, close_services


def create_app(services: Services | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if services is not None:
            yield
            return
        sv = await build_services()
        app.state.services = sv
        await sv.registry.refresh()
        try:
            yield
        finally:
            await close_services(sv)

    app = FastAPI(title="Trend Finder", version="0.1.0", lifespan=lifespan)
    if services is not None:
        app.state.services = services
    app.include_router(health.router, prefix="/api")
    app.include_router(providers.router, prefix="/api")
    return app


app = create_app()
