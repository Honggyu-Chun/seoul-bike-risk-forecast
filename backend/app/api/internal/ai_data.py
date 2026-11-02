from fastapi import APIRouter

# stations / observations / forecasts 페이지 API
router = APIRouter(prefix="/internal/ai-data/v1", tags=["ai-data"])
