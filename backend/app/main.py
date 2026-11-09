from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.account import auth, favorites, me
from app.api.internal import ai_data
from app.api.public import places, predictions, recommendations, stations, status
from app.clients.ai import AIClient
from app.clients.kakao import KakaoLocalClient
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.log_filters import install_privacy_filters
from app.db.session import make_engine, make_sessionmaker


def create_app(settings: Settings | None = None, ai_client: AIClient | None = None,
               kakao_client: KakaoLocalClient | None = None) -> FastAPI:
    settings = settings or get_settings()
    install_privacy_filters()

    engine = make_engine(settings.database_url) if settings.database_url else None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        await app.state.ai.aclose()
        await app.state.kakao.aclose()
        if engine is not None:
            await engine.dispose()

    app = FastAPI(title="CycleOn backend", lifespan=lifespan)
    app.state.settings = settings
    app.state.ai = ai_client or AIClient(settings)
    app.state.kakao = kakao_client or KakaoLocalClient(settings)
    app.state.db = make_sessionmaker(engine) if engine is not None else None
    # allow_credentials: 프론트가 다른 출처(다른 서브도메인)일 때 세션 쿠키를 주고받게 한다.
    # 리버스 프록시·Vite 프록시로 같은 출처에서 부르면 CORS 자체가 쓰이지 않는다.
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type"])
    # 대여소 목록(수천 곳, 수량 포함)처럼 큰 JSON을 압축해 보낸다. 1KB보다 작은 응답은 그대로
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    register_error_handlers(app)
    app.include_router(ai_data.router)
    for module in (status, stations, predictions, recommendations, places, auth, me, favorites):
        app.include_router(module.router, prefix="/api")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
