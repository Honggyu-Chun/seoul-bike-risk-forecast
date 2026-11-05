from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.internal import ai_data
from app.api.public import predictions, recommendations, stations
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="CycleOn backend")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
    register_error_handlers(app)
    app.include_router(ai_data.router)
    for module in (stations, predictions, recommendations):
        app.include_router(module.router, prefix="/api")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
