"""프로필 사진 (/api/me/avatar). 로그인 필요, 본인 사진만 다룬다.

올리기는 multipart 폼이 아니라 이미지 파일을 본문 그대로 받는다(python-multipart 의존성이 필요 없고, 프론트도
fetch(url, {method: "PUT", body: file, headers: {"Content-Type": file.type}}) 한 줄이면 된다).
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.account.me import current_account
from app.api.deps import IMAGE_TYPES, get_now, get_session, require_trusted_image_origin, require_trusted_origin
from app.schemas.account import ProfileResponse
from app.services import avatars as service
from app.services.accounts import Account

router = APIRouter(prefix="/me", tags=["me"])

# /docs에서 파일을 골라 보낼 수 있게 요청 본문 모양을 알려 준다(본문을 직접 읽으므로 FastAPI가 만들지 못한다)
IMAGE_BODY = {"required": True,
              "content": {t: {"schema": {"type": "string", "format": "binary"}} for t in IMAGE_TYPES}}


@router.put("/avatar", response_model=ProfileResponse, dependencies=[Depends(require_trusted_image_origin)],
            openapi_extra={"requestBody": IMAGE_BODY})
async def upload_avatar(request: Request, session: AsyncSession = Depends(get_session),
                        account: Account = Depends(current_account), now: datetime = Depends(get_now)):
    """사진 올리기·바꾸기. 본문은 JPEG·PNG·WebP 파일 그대로(Content-Type: image/jpeg·png·webp), 5MB 이하.

    256×256 WebP로 새로 만들어 저장하고 원본(EXIF GPS 등 메타데이터 포함)은 버린다. 바뀐 avatar_url이 든 프로필을 준다.
    5MB 초과·픽셀 수 초과는 413 image_too_large, 다른 형식은 415 unsupported_image, 깨진 파일은 422 invalid_image.
    """
    return await service.upload(session, account, await service.read_upload(request), now)


@router.get("/avatar", response_class=Response,
            responses={200: {"content": {"image/webp": {}}, "description": "256×256 WebP"}})
async def get_avatar(session: AsyncSession = Depends(get_session), account: Account = Depends(current_account)):
    """내 사진(256×256 WebP). 없으면 404 avatar_not_found. 프론트는 avatar_url을 <img src>에 그대로 쓴다.

    하루 동안 브라우저에 캐시한다. 사진을 바꾸면 avatar_url의 v가 바뀌어 새로 받는다.
    """
    return Response(await service.image(session, account), media_type="image/webp",
                    headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


@router.delete("/avatar", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_trusted_origin)])
async def delete_avatar(session: AsyncSession = Depends(get_session), account: Account = Depends(current_account)):
    """사진 지우기. 없어도 204."""
    await service.delete(session, account)
