"""프로필 사진 변환. 올라온 사진을 256×256 WebP로 새로 만든다.

원본은 저장하지 않으므로 촬영 위치(EXIF GPS)·기기 정보 같은 메타데이터가 남지 않는다.
CPU를 쓰는 동기 함수라 호출하는 쪽이 스레드풀에서 돌린다(bcrypt와 같음).

받는 형식 → 이 모듈이 내는 것
- JPEG·PNG·WebP                     → WebP 바이트
- 그 밖의 형식(GIF, SVG, HEIC 등)     → UnsupportedImage(415)
- 열 수 없거나 깨진 파일              → InvalidImage(422)
- 픽셀 수가 MAX_PIXELS를 넘음          → ImageTooLarge(413). 작은 파일이 메모리를 크게 잡아먹는 압축 폭탄 방지.
                                        헤더의 가로·세로만 보고 디코딩하기 전에 막는다
"""
import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.errors import ImageTooLarge, InvalidImage, UnsupportedImage

SIZE = 256
FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_PIXELS = 25_000_000      # 5000×5000. 디코딩하면 RGBA 기준 약 100MB
WEBP_QUALITY = 85


def make_avatar(data: bytes) -> bytes:
    """사진 바이트 → 256×256 WebP 바이트. EXIF 방향을 적용하고 가운데를 정사각형으로 자른다."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            # 형식 판별은 헤더만 읽는다. 받지 않는 흔한 형식(GIF·BMP)도 알아봐야 415로 구분해 답할 수 있다.
            # 그 밖의 형식 해석기(TIFF 등)는 아예 돌리지 않는다.
            image = Image.open(io.BytesIO(data), formats=[*sorted(FORMATS), "GIF", "BMP"])
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ImageTooLarge() from None          # Pillow 기본 상한(약 8,900만 픽셀)도 넘는 그림
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise InvalidImage() from None
    with image:
        if image.format not in FORMATS:
            raise UnsupportedImage()
        width, height = image.size
        if width * height > MAX_PIXELS:
            raise ImageTooLarge()
        try:
            if image.format == "JPEG":
                image.draft("RGB", (SIZE, SIZE))   # JPEG는 줄여 가며 디코딩해 메모리를 아낀다
            upright = ImageOps.exif_transpose(image)
            upright = upright.convert("RGBA" if _has_alpha(upright) else "RGB")
            square = ImageOps.fit(upright, (SIZE, SIZE), method=Image.Resampling.LANCZOS)
        except (OSError, ValueError, SyntaxError):
            raise InvalidImage() from None   # 헤더는 맞지만 본문이 잘린·깨진 파일
    square.info.clear()                          # 원본의 EXIF·ICC·XMP를 옮기지 않는다
    out = io.BytesIO()
    square.save(out, "WEBP", quality=WEBP_QUALITY, method=4)
    return out.getvalue()


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)
