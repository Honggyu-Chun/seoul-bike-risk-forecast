"""프로필 사진 API 테스트 (PUT·GET·DELETE /api/me/avatar, 프로필 응답의 avatar_url).

테스트 DB를 쓰고, bcrypt 비용은 4로 낮춘다. 사진은 Pillow로 그때그때 만든다.
"""
import io
import struct
import zlib

import pytest
from PIL import Image

from app.core import images
from tests.test_account_api import ORIGIN, PASSWORD, clock, make_client, run_sql  # noqa: F401 (fixture)

JPEG, PNG, WEBP = "image/jpeg", "image/png", "image/webp"
PNG_SIGNATURE = bytes([0x89]) + b"PNG\r\n" + bytes([0x1A]) + b"\n"


def picture(fmt="JPEG", size=(400, 300), color="red", **save):
    buffer = io.BytesIO()
    Image.new("RGBA" if fmt == "PNG" else "RGB", size, color).save(buffer, fmt, **save)
    return buffer.getvalue()


def gps_jpeg():
    """촬영 위치(GPS)와 방향(6: 90도 돌림)이 든 400×200 JPEG"""
    exif = Image.Exif()
    exif[0x0112] = 6
    exif[0x8825] = {1: "N", 2: (37.0, 33.0, 12.0), 3: "E", 4: (126.0, 58.0, 41.0)}
    buffer = io.BytesIO()
    Image.new("RGB", (400, 200), "blue").save(buffer, "JPEG", exif=exif.tobytes())
    return buffer.getvalue()


@pytest.fixture
def client(make_client):
    client = make_client()
    signup = client.post("/api/auth/signup", json={"login_id": "rider", "password": PASSWORD}, headers=ORIGIN)
    assert signup.status_code == 201 and signup.json()["avatar_url"] is None
    return client


def upload(client, body, content_type=JPEG, headers=ORIGIN):
    return client.put("/api/me/avatar", content=body, headers={**headers, "Content-Type": content_type})


def stored(client):
    response = client.get("/api/me/avatar")
    assert response.status_code == 200
    return Image.open(io.BytesIO(response.content))


@pytest.mark.parametrize("fmt,content_type", [("JPEG", JPEG), ("PNG", PNG), ("WEBP", WEBP)])
def test_upload_makes_256_webp(client, fmt, content_type):
    response = upload(client, picture(fmt), content_type)
    assert response.status_code == 200
    body = response.json()
    assert body["login_id"] == "rider" and body["avatar_url"].startswith("/api/me/avatar?v=")
    image = stored(client)
    assert image.format == "WEBP" and image.size == (256, 256)


def test_get_avatar_headers(client):
    upload(client, picture())
    response = client.get("/api/me/avatar")
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"] == "private, max-age=86400"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_gps_and_other_metadata_are_dropped(client):
    assert Image.open(io.BytesIO(gps_jpeg())).getexif().get(0x8825)   # 원본에는 GPS가 있다
    upload(client, gps_jpeg())
    image = stored(client)
    assert not image.getexif() and "exif" not in image.info and "icc_profile" not in image.info
    raw = client.get("/api/me/avatar").content
    assert b"EXIF" not in raw and b"Exif" not in raw and b"XMP" not in raw


def test_exif_orientation_is_applied_before_cropping():
    """400×200 가로 사진이 EXIF 방향 6이면 세로(200×400)로 세운 뒤 가운데를 자른다"""
    exif = Image.Exif()
    exif[0x0112] = 6
    canvas = Image.new("RGB", (400, 200), "white")
    canvas.paste((255, 0, 0), (0, 0, 200, 200))   # 왼쪽 절반 빨강 → 세우면 위쪽 절반이 빨강
    buffer = io.BytesIO()
    canvas.save(buffer, "JPEG", exif=exif.tobytes(), quality=95)
    out = Image.open(io.BytesIO(images.make_avatar(buffer.getvalue()))).convert("RGB")
    top, bottom = out.getpixel((128, 10)), out.getpixel((128, 245))
    assert top[0] > 200 and top[1] < 80          # 위: 빨강
    assert bottom == pytest.approx((255, 255, 255), abs=40)   # 아래: 흰색


def test_center_crop_keeps_the_middle():
    canvas = Image.new("RGB", (900, 300), "black")
    canvas.paste((0, 255, 0), (300, 0, 600, 300))   # 가운데 정사각형만 초록
    buffer = io.BytesIO()
    canvas.save(buffer, "PNG")
    out = Image.open(io.BytesIO(images.make_avatar(buffer.getvalue()))).convert("RGB")
    assert out.getpixel((10, 128))[1] > 200 and out.getpixel((245, 128))[1] > 200


def test_avatar_url_changes_when_replaced(client, clock):
    first = upload(client, picture(color="red")).json()["avatar_url"]
    clock.advance(seconds=5)
    second = upload(client, picture(color="green")).json()["avatar_url"]
    assert first != second
    assert client.get("/api/me").json()["avatar_url"] == second
    assert stored(client).convert("RGB").getpixel((128, 128))[1] > 100   # 새 사진


def test_login_response_has_avatar_url(client):
    url = upload(client, picture()).json()["avatar_url"]
    client.post("/api/auth/logout", headers=ORIGIN)
    login = client.post("/api/auth/login", json={"login_id": "rider", "password": PASSWORD}, headers=ORIGIN)
    assert login.json()["avatar_url"] == url


def test_profile_update_keeps_avatar_url(client):
    url = upload(client, picture()).json()["avatar_url"]
    assert client.patch("/api/me", json={"nickname": "따릉"}, headers=ORIGIN).json()["avatar_url"] == url


def test_delete(client):
    upload(client, picture())
    assert client.delete("/api/me/avatar", headers=ORIGIN).status_code == 204
    assert client.get("/api/me/avatar").status_code == 404
    assert client.get("/api/me/avatar").json()["error"]["code"] == "avatar_not_found"
    assert client.get("/api/me").json()["avatar_url"] is None
    assert client.delete("/api/me/avatar", headers=ORIGIN).status_code == 204   # 없어도 204


def test_no_avatar_is_404(client):
    response = client.get("/api/me/avatar")
    assert response.status_code == 404 and response.json()["error"]["code"] == "avatar_not_found"


def test_too_large_by_content_length(client):
    response = upload(client, b"\0" * (5 * 1024 * 1024 + 1))
    assert response.status_code == 413 and response.json()["error"]["code"] == "image_too_large"


def test_too_large_without_content_length(client):
    """Content-Length 없이(chunked) 보내도 읽으면서 5MB에서 멈춘다"""
    def chunks():
        for _ in range(6):
            yield b"\0" * (1024 * 1024)
    response = client.put("/api/me/avatar", content=chunks(), headers={**ORIGIN, "Content-Type": JPEG})
    assert response.status_code == 413 and response.json()["error"]["code"] == "image_too_large"


def test_too_many_pixels(client, monkeypatch):
    monkeypatch.setattr(images, "MAX_PIXELS", 400 * 300 - 1)
    response = upload(client, picture(size=(400, 300)))
    assert response.status_code == 413 and response.json()["error"]["code"] == "image_too_large"


def bomb_png(side):
    """side×side 흑백 PNG. 픽셀이 모두 0이라 파일은 작지만 디코딩하면 side² 바이트가 된다"""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    packer, row = zlib.compressobj(9), bytes(side + 1)
    pixels = b"".join(packer.compress(row) for _ in range(side)) + packer.flush()
    header = struct.pack(">IIBBBBB", side, side, 8, 0, 0, 0, 0)
    return PNG_SIGNATURE + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


@pytest.mark.parametrize("side", [6_000, 20_000])   # 3,600만 픽셀(우리 상한 초과), 4억 픽셀(Pillow 상한도 초과)
def test_decompression_bomb_is_rejected_before_decoding(client, side):
    body = bomb_png(side)
    assert len(body) < 5 * 1024 * 1024
    response = upload(client, body, PNG)
    assert response.status_code == 413 and response.json()["error"]["code"] == "image_too_large"


@pytest.mark.parametrize("body,content_type", [
    (picture("GIF"), "image/jpeg"),                                       # 실제 형식이 GIF
    (b'<svg xmlns="http://www.w3.org/2000/svg"/>', "image/svg+xml"),        # 허용하지 않는 Content-Type
    (picture("GIF"), "image/gif"),
    (picture(), "application/octet-stream"),
])
def test_unsupported_format_is_415(client, body, content_type):
    response = upload(client, body, content_type)
    assert response.status_code == 415 and response.json()["error"]["code"] == "unsupported_image"


@pytest.mark.parametrize("body", [b"not an image", picture()[:200], b""])
def test_broken_file_is_422(client, body):
    response = upload(client, body)
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_image"


def test_json_body_is_415_not_saved(client):
    response = client.put("/api/me/avatar", json={"image": "x"}, headers=ORIGIN)
    assert response.status_code == 415 and response.json()["error"]["code"] == "unsupported_image"


def test_requires_login(make_client):
    client = make_client()
    assert upload(client, picture()).status_code == 401
    assert client.get("/api/me/avatar").status_code == 401
    assert client.delete("/api/me/avatar", headers=ORIGIN).status_code == 401


def test_other_origin_is_403(client):
    evil = {"Origin": "http://evil.example"}
    assert upload(client, picture(), headers=evil).json()["error"]["code"] == "origin_not_allowed"
    assert client.delete("/api/me/avatar", headers=evil).status_code == 403


def test_form_upload_from_other_site_is_blocked(client):
    """다른 사이트의 HTML 폼은 image/* Content-Type을 보낼 수 없다(multipart·text/plain만). Origin이 없어도 막는다"""
    response = client.put("/api/me/avatar", content=picture(), headers={"Content-Type": "text/plain"})
    assert response.status_code == 415


def test_account_deletion_removes_avatar(client, test_database_url):
    upload(client, picture())
    assert run_sql(test_database_url, "SELECT count(*) FROM user_avatars")[0][0][0] == 1
    response = client.request("DELETE", "/api/me", json={"password": PASSWORD}, headers=ORIGIN)
    assert response.status_code == 204
    assert run_sql(test_database_url, "SELECT count(*) FROM user_avatars")[0][0][0] == 0


def test_avatar_is_per_user(client):
    upload(client, picture())
    client.post("/api/auth/logout", headers=ORIGIN)
    client.post("/api/auth/signup", json={"login_id": "other", "password": PASSWORD}, headers=ORIGIN)
    assert client.get("/api/me/avatar").status_code == 404
    assert client.get("/api/me").json()["avatar_url"] is None
