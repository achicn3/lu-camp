"""菜單照片 API（docs/44 §3.4；O1d）：上傳、公開讀取、移除、去重、權限、稽核。

照片存在資料庫（店主 2026-10-02 裁示），每晚備份與還原演練自動涵蓋。
讀取端點**不需登入**：`<img>` 帶不了 Bearer，照片本來就要公開在線上菜單上；
網址是內容雜湊，猜不到也列舉不了。
"""

import hashlib
import io
from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import PHOTO_MAX_BODY_BYTES, create_app
from app.modules.menu.models import MenuPhoto
from app.modules.menu.photos import MAX_UPLOAD_BYTES
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole

_EXIF_GPS_IFD = 0x8825


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _seed(session: AsyncSession, name: str = "門市") -> tuple[str, str]:
    store = Store(name=name)
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username=f"clk-{name}", password_hash="h", role=UserRole.CLERK)
    mgr = User(store_id=store.id, username=f"mgr-{name}", password_hash="h", role=UserRole.MANAGER)
    session.add_all([clerk, mgr])
    await session.flush()
    return (
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _jpeg(
    color: tuple[int, int, int] = (200, 100, 50), size: tuple[int, int] = (1600, 1200)
) -> bytes:
    exif = Image.Exif()
    exif.get_ifd(_EXIF_GPS_IFD)[2] = (25.0, 2.0, 0.0)
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


async def _item(client: httpx.AsyncClient, token: str, name: str = "戚風") -> int:
    resp = await client.post(
        "/api/v1/menu-items", json={"name": name, "unit_price": "90"}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    item_id: int = resp.json()["id"]
    return item_id


async def _upload(
    client: httpx.AsyncClient, token: str, item_id: int, data: bytes, filename: str = "cake.jpg"
) -> httpx.Response:
    return await client.post(
        f"/api/v1/menu-items/{item_id}/photo",
        files={"file": (filename, data, "image/jpeg")},
        headers=_auth(token),
    )


async def test_upload_then_public_read_without_gps(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr = await _seed(db_session)
    item_id = await _item(client, mgr)
    resp = await _upload(client, mgr, item_id, _jpeg())
    assert resp.status_code == 200, resp.text
    sha = resp.json()["photo_sha256"]
    assert len(sha) == 64

    listed = await client.get("/api/v1/menu-items", headers=_auth(mgr))
    assert [i["photo_sha256"] for i in listed.json()] == [sha]

    photo = await client.get(f"/api/v1/menu-photos/{sha}.webp")  # 不帶登入
    assert photo.status_code == 200
    assert photo.headers["content-type"] == "image/webp"
    assert "immutable" in photo.headers["cache-control"]
    assert hashlib.sha256(photo.content).hexdigest() == sha
    image = Image.open(io.BytesIO(photo.content))
    assert image.size == (1200, 900)
    assert len(image.getexif()) == 0


async def test_same_photo_on_two_items_is_stored_once(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr = await _seed(db_session)
    data = _jpeg()
    a = await _upload(client, mgr, await _item(client, mgr, "戚風"), data)
    b = await _upload(client, mgr, await _item(client, mgr, "司康"), data)
    assert a.json()["photo_sha256"] == b.json()["photo_sha256"]
    assert await db_session.scalar(select(func.count()).select_from(MenuPhoto)) == 1


async def test_replace_and_remove_photo_are_audited(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr = await _seed(db_session)
    item_id = await _item(client, mgr)
    first = (await _upload(client, mgr, item_id, _jpeg((1, 2, 3)))).json()["photo_sha256"]
    second = (await _upload(client, mgr, item_id, _jpeg((200, 2, 3)))).json()["photo_sha256"]
    assert first != second
    removed = await client.delete(f"/api/v1/menu-items/{item_id}/photo", headers=_auth(mgr))
    assert removed.status_code == 200, removed.text
    assert removed.json()["photo_sha256"] is None
    # 移除只是不再引用，舊網址仍可讀（已發佈的線上菜單快照可能還指著它）。
    assert (await client.get(f"/api/v1/menu-photos/{first}.webp")).status_code == 200
    logs = (
        await db_session.scalars(
            select(AuditLog)
            .where(AuditLog.action == "UPDATE_MENU_ITEM_PHOTO")
            .order_by(AuditLog.id)
        )
    ).all()
    assert [(log.before, log.after) for log in logs] == [
        ({"photo_sha256": None}, {"photo_sha256": first}),
        ({"photo_sha256": first}, {"photo_sha256": second}),
        ({"photo_sha256": second}, {"photo_sha256": None}),
    ]


async def test_clerk_cannot_upload(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    clerk, mgr = await _seed(db_session)
    item_id = await _item(client, mgr)
    assert (await _upload(client, clerk, item_id, _jpeg())).status_code == 403
    assert (
        await client.delete(f"/api/v1/menu-items/{item_id}/photo", headers=_auth(clerk))
    ).status_code == 403


async def test_upload_requires_login(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, mgr = await _seed(db_session)
    item_id = await _item(client, mgr)
    resp = await client.post(
        f"/api/v1/menu-items/{item_id}/photo", files={"file": ("a.jpg", _jpeg(), "image/jpeg")}
    )
    assert resp.status_code == 401


async def test_invalid_file_is_rejected_and_item_unchanged(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr = await _seed(db_session)
    item_id = await _item(client, mgr)
    resp = await _upload(client, mgr, item_id, b"%PDF-1.4 not a photo", "menu.pdf")
    assert resp.status_code == 422
    assert "JPEG" in resp.json()["detail"]
    listed = await client.get("/api/v1/menu-items", headers=_auth(mgr))
    assert listed.json()[0]["photo_sha256"] is None
    assert await db_session.scalar(select(func.count()).select_from(MenuPhoto)) == 0


async def test_too_large_upload_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr = await _seed(db_session)
    item_id = await _item(client, mgr)
    resp = await _upload(client, mgr, item_id, b"\xff" * (MAX_UPLOAD_BYTES + 1))
    assert resp.status_code == 413
    assert "10 MB" in resp.json()["detail"]


async def test_other_store_item_is_not_found(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr_a = await _seed(db_session, "A")
    _, mgr_b = await _seed(db_session, "B")
    item_id = await _item(client, mgr_a)
    assert (await _upload(client, mgr_b, item_id, _jpeg())).status_code == 404


async def test_unknown_or_malformed_photo_key_is_not_found(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    assert (await client.get(f"/api/v1/menu-photos/{'0' * 64}.webp")).status_code == 404
    assert (await client.get("/api/v1/menu-photos/..%2F..%2Fetc.webp")).status_code == 404
    assert (await client.get(f"/api/v1/menu-photos/{'A' * 64}.webp")).status_code == 404


async def test_oversized_body_is_rejected_before_parsing_even_without_login(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Codex 對抗審查 O1d：multipart 會在檢查檔案大小（甚至驗登入）之前就整包解析、落暫存檔，
    所以要在解析**前**用 Content-Length 擋——連沒登入的請求都會先被解析，不能只靠 handler。"""
    _, mgr = await _seed(db_session)
    item_id = await _item(client, mgr)
    huge = str(PHOTO_MAX_BODY_BYTES + 1)
    resp = await client.post(
        f"/api/v1/menu-items/{item_id}/photo",
        content=b"x",
        headers={"Content-Length": huge, "Content-Type": "multipart/form-data; boundary=b"},
    )
    assert resp.status_code == 413
    assert "10 MB" in resp.json()["detail"]


async def test_streamed_body_without_length_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr = await _seed(db_session)
    item_id = await _item(client, mgr)

    async def chunks() -> AsyncGenerator[bytes]:
        yield b"--b\r\n"

    resp = await client.post(
        f"/api/v1/menu-items/{item_id}/photo",
        content=chunks(),
        headers={**_auth(mgr), "Content-Type": "multipart/form-data; boundary=b"},
    )
    assert resp.status_code == 411
