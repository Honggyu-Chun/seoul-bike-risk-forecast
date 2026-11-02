from fastapi import FastAPI

from app.api.internal import ai_data
from app.api.public import predictions, recommendations, stations


def create_app() -> FastAPI:
    app = FastAPI(title="CycleOn backend")
    app.include_router(ai_data.router)
    for module in (stations, predictions, recommendations):
        app.include_router(module.router, prefix="/api")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
