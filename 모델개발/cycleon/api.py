"""Separate reference backend and AI apps; no changes to legacy port 8000."""
from contextlib import asynccontextmanager
from datetime import datetime
import asyncio
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from .common import instant
from .contracts import (PredictionRequest, RecommendationRequest, PredictionResponse, RecommendationResponse,
                        StatusResponse, Page, Station, Observation, Forecast)


def authenticator(token):
    if not token or len(token) < 32:
        raise ValueError("A private service token of at least 32 characters is required")
    bearer = HTTPBearer(auto_error=False)

    def auth(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if not credentials or not secrets.compare_digest(credentials.credentials, token):
            raise HTTPException(status_code=401, detail="service_authentication_required")
    return auth


def backend_app(store, token):
    app = FastAPI(title="CycleOn backend data reference", version="1.0.0", dependencies=[Depends(authenticator(token))])
    def page(table, cursor, limit, source, start, end, watermark):
        try:
            return store.page(table, cursor=cursor, limit=limit, source=source,
                              start=instant(start) if start else None, end=instant(end) if end else None, watermark=watermark)
        except ValueError:
            raise HTTPException(422, "invalid_time_or_cursor") from None

    @app.get("/internal/ai-data/v1/stations", response_model=Page[Station])
    def stations(cursor: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=10000), watermark: int | None = Query(None, ge=0)):
        return page("stations", cursor, limit, None, None, None, watermark)

    @app.get("/internal/ai-data/v1/observations", response_model=Page[Observation])
    def observations(cursor: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=10000), source: str | None = None,
                     start: datetime | None = None, end: datetime | None = None, watermark: int | None = Query(None, ge=0)):
        return page("observations", cursor, limit, source, start, end, watermark)

    @app.get("/internal/ai-data/v1/forecasts", response_model=Page[Forecast])
    def forecasts(cursor: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=10000), source: str | None = None,
                  start: datetime | None = None, end: datetime | None = None, watermark: int | None = Query(None, ge=0)):
        return page("forecasts", cursor, limit, source, start, end, watermark)
    return app


def ai_app(engine, token, backend=None):
    async def refresh():
        while True:
            try:
                if backend:
                    await asyncio.to_thread(backend.sync)
                await asyncio.to_thread(engine.precompute)
            except Exception as error:
                # Never propagate URLs, keys, response bodies or database paths.
                engine.compute_error = type(error).__name__
            await asyncio.sleep(20)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(refresh())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if backend:
            backend.close()

    app = FastAPI(title="CycleOn AI service", version="1.0.0", lifespan=lifespan,
                  dependencies=[Depends(authenticator(token))])

    @app.get("/v1/status", response_model=StatusResponse)
    def status():
        return engine.status()

    @app.post("/v1/predictions", response_model=PredictionResponse)
    def predictions(request: PredictionRequest):
        return engine.predict(request.station_ids, request.horizons_hours)

    @app.post("/v1/recommendations", response_model=RecommendationResponse)
    def recommendations(request: RecommendationRequest):
        return engine.recommend(request.latitude, request.longitude, request.horizon_hours, request.radius_m)
    return app
