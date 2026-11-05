from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.internal import ai_data
from app.api.public import predictions, recommendations, stations, status
from app.clients.ai import AIClient
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers


def create_app(settings: Settings | None = None, ai_client: AIClient | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.ai.aclose()

    app = FastAPI(title="CycleOn backend", lifespan=lifespan)
    app.state.settings = settings
    app.state.ai = ai_client or AIClient(settings)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
    register_error_handlers(app)
    app.include_router(ai_data.router)
    for module in (status, stations, predictions, recommendations):
        app.include_router(module.router, prefix="/api")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
