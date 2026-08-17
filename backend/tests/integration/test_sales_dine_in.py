"""餐飲內用/外帶與桌號（docs/35）。

`service_mode` / `table_no` 是**純資訊欄位**——不進入任何金額、稅、折扣、點數計算，
本檔只驗它們的不變量：

- 有餐飲明細 ⇔ 必須宣告內用/外帶（跨表規則，DB 的 CHECK 看不到 sale_lines → service 守）
- 內用必有桌號、外帶必無桌號（單列自洽 → DB CHECK 也守一次，含 NULL 安全性）
- 桌號必須是設定頁維護過的那幾桌
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.customerdisplay.schemas import CartUpsertRequest, StaffCartPayloadRead
from app.modules.inventory.models import CatalogProduct
from app.modules.menu.models import MenuItem
from app.modules.sales.models import Sale
from app.modules.settings.schemas import SettingsUpdateRequest
from app.modules.settings.service import StoreSettingsService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import ServiceMode, UserRole
from tests.integration.customer_display_helpers import CustomerDisplayAwareClient

_TABLES = ["A1", "A2", "B1"]


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with CustomerDisplayAwareClient(
        transport=transport, base_url="http://test", db_session=db_session
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def _seed(session: AsyncSession, *, tables: list[str] | None = None) -> tuple[str, int, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("1000"))
    await StoreSettingsService(session).update_settings(
        store.id,
        actor_user_id=None,
        patch=SettingsUpdateRequest(dine_in_tables=_TABLES if tables is None else tables),
    )
    await session.flush()
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return token, store.id, clerk.id


async def _menu_item(session: AsyncSession, store_id: int) -> int:
    item = MenuItem(store_id=store_id, name="手沖-耶加", unit_price=Decimal("180"))
    session.add(item)
    await session.flush()
    return item.id


def _auth(token: str, idem: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": idem}


async def _post_menu_sale(
    client: httpx.AsyncClient,
    token: str,
    menu_item_id: int,
    idem: str,
    **extra: object,
) -> httpx.Response:
    body: dict[str, object] = {
        "lines": [{"line_type": "MENU", "menu_item_id": menu_item_id, "qty": 1}]
    }
    body.update(extra)
    return await client.post("/api/v1/sales", json=body, headers=_auth(token, idem))


# ── service 層：跨表規則 ──


async def test_dine_in_sale_records_table_no(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    item = await _menu_item(db_session, store_id)
    resp = await _post_menu_sale(
        client, token, item, "d1", service_mode="DINE_IN", table_no="A2"
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["service_mode"] == "DINE_IN"
    assert body["table_no"] == "A2"
    sale = await db_session.scalar(select(Sale).where(Sale.id == body["id"]))
    assert sale is not None
    assert sale.service_mode is ServiceMode.DINE_IN
    assert sale.table_no == "A2"


async def test_takeout_sale_has_no_table_no(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    item = await _menu_item(db_session, store_id)
    resp = await _post_menu_sale(client, token, item, "t1", service_mode="TAKEOUT")
    assert resp.status_code == 201, resp.text
    assert resp.json()["service_mode"] == "TAKEOUT"
    assert resp.json()["table_no"] is None


async def test_menu_sale_without_service_mode_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """有餐飲卻沒宣告內用/外帶 → 422。出餐單印不出桌號，東西送不出去。"""
    token, store_id, _ = await _seed(db_session)
    item = await _menu_item(db_session, store_id)
    resp = await _post_menu_sale(client, token, item, "n1")
    assert resp.status_code == 422
    assert "內用或外帶" in resp.json()["detail"]


async def test_dine_in_without_table_no_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    item = await _menu_item(db_session, store_id)
    resp = await _post_menu_sale(client, token, item, "n2", service_mode="DINE_IN")
    assert resp.status_code == 422
    assert "桌號" in resp.json()["detail"]


async def test_takeout_with_table_no_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed(db_session)
    item = await _menu_item(db_session, store_id)
    resp = await _post_menu_sale(
        client, token, item, "n3", service_mode="TAKEOUT", table_no="A1"
    )
    assert resp.status_code == 422


async def test_unknown_table_no_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """桌號必須在設定清單內：自由字串會長出「5」「5桌」「五」三種寫法指同一桌。"""
    token, store_id, _ = await _seed(db_session)
    item = await _menu_item(db_session, store_id)
    resp = await _post_menu_sale(
        client, token, item, "n4", service_mode="DINE_IN", table_no="Z9"
    )
    assert resp.status_code == 422
    assert "桌號清單" in resp.json()["detail"]


async def test_dine_in_blocked_when_table_list_empty(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """桌號清單還沒維護 → 內用一律擋（fail closed，不讓自由打字繞過）。"""
    token, store_id, _ = await _seed(db_session, tables=[])
    item = await _menu_item(db_session, store_id)
    resp = await _post_menu_sale(
        client, token, item, "n5", service_mode="DINE_IN", table_no="A1"
    )
    assert resp.status_code == 422


async def test_table_no_is_stripped(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    token, store_id, _ = await _seed(db_session)
    item = await _menu_item(db_session, store_id)
    resp = await _post_menu_sale(
        client, token, item, "s1", service_mode="DINE_IN", table_no="  A1  "
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["table_no"] == "A1"


async def test_non_menu_sale_cannot_declare_service_mode(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """純二手/一般商品的單標成「內用」→ 422：會印出根本不存在的餐點，也污染統計。"""
    token, store_id, _ = await _seed(db_session)
    product = CatalogProduct(
        store_id=store_id, sku="SKU9", name="二手雜物", unit_price=Decimal("100"),
        quantity_on_hand=5,
    )
    db_session.add(product)
    await db_session.flush()
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [{"line_type": "CATALOG", "catalog_product_id": product.id, "qty": 1}],
            "service_mode": "DINE_IN",
            "table_no": "A1",
        },
        headers=_auth(token, "n6"),
    )
    assert resp.status_code == 422


# ── DB CHECK：單列自洽（NULL 安全） ──


async def _insert_sale_raw(
    session: AsyncSession, store_id: int, clerk_id: int, mode: str | None, table_no: str | None
) -> None:
    await session.execute(
        text(
            "INSERT INTO sales"
            " (store_id, clerk_user_id, subtotal, tax, total, payment_method,"
            "  invoice_status, status, service_mode, table_no, created_at, updated_at)"
            " VALUES (:sid, :uid, 0, 0, 0, 'CASH', 'NOT_ISSUED', 'COMPLETED',"
            "  :mode, :table_no, now(), now())"
        ),
        {"sid": store_id, "uid": clerk_id, "mode": mode, "table_no": table_no},
    )


@pytest.mark.parametrize(
    ("mode", "table_no"),
    [
        ("DINE_IN", None),  # 內用缺桌號
        ("TAKEOUT", "A1"),  # 外帶夾帶桌號
        (None, "A1"),  # 沒宣告卻有桌號——若 CHECK 寫成等式簡寫，這一列會被 NULL 放行
    ],
)
async def test_db_check_rejects_inconsistent_service_mode(
    db_session: AsyncSession, mode: str | None, table_no: str | None
) -> None:
    _, store_id, clerk_id = await _seed(db_session)
    with pytest.raises(IntegrityError):
        await _insert_sale_raw(db_session, store_id, clerk_id, mode, table_no)
    await db_session.rollback()


@pytest.mark.parametrize(
    ("mode", "table_no"),
    [("DINE_IN", "A1"), ("TAKEOUT", None), (None, None)],
)
async def test_db_check_accepts_valid_combinations(
    db_session: AsyncSession, mode: str | None, table_no: str | None
) -> None:
    _, store_id, clerk_id = await _seed(db_session)
    await _insert_sale_raw(db_session, store_id, clerk_id, mode, table_no)
    await db_session.flush()


# ── 冪等指紋 ──


async def test_same_key_different_table_is_a_conflict_not_a_replay(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同 key、同品項、換一桌 → 409 而非靜默回放原單。

    桌號雖不影響金額，卻決定出餐單印到哪一桌；若不納入指紋，A1 的單會被當成 A2 的重放，
    第二桌的餐點就掛在第一桌名下。
    """
    token, store_id, _ = await _seed(db_session)
    item = await _menu_item(db_session, store_id)
    first = await _post_menu_sale(
        client, token, item, "dup1", service_mode="DINE_IN", table_no="A1"
    )
    assert first.status_code == 201, first.text
    again = await _post_menu_sale(
        client, token, item, "dup1", service_mode="DINE_IN", table_no="A2"
    )
    assert again.status_code == 409, again.text


async def test_same_key_same_table_still_replays(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """真正的網路重試（內容完全相同）仍須冪等回放原單，不可因新欄位而誤判衝突。"""
    token, store_id, _ = await _seed(db_session)
    item = await _menu_item(db_session, store_id)
    first = await _post_menu_sale(
        client, token, item, "same1", service_mode="DINE_IN", table_no="A1"
    )
    assert first.status_code == 201, first.text
    again = await _post_menu_sale(
        client, token, item, "same1", service_mode="DINE_IN", table_no="A1"
    )
    assert again.status_code in (200, 201), again.text
    assert again.json()["id"] == first.json()["id"]


async def test_cart_upsert_round_trips_service_mode(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """購物車必須保存內用/外帶與桌號（docs/35）。

    回歸測試：POS 重新載入被凍結的購物車時是從 `staff_payload` 還原的；少了這兩欄，
    選擇會遺失，而凍結中兩顆模式鍵都停用 → 已簽名的交易只能作廢重簽。
    """
    payload = CartUpsertRequest.model_validate(
        {
            "lines": [{"line_type": "MENU", "menu_item_id": 1, "qty": 1}],
            "service_mode": "DINE_IN",
            "table_no": "  A1  ",
        }
    )
    assert payload.service_mode is ServiceMode.DINE_IN
    assert payload.table_no == "A1"  # 與 SaleCreateRequest 同一條正規化規則
    dumped = payload.model_dump(mode="json")
    assert dumped["service_mode"] == "DINE_IN"
    assert dumped["table_no"] == "A1"
    # 還原端（POS 讀 staff_payload 用的 schema）必須看得到這兩欄
    restored = StaffCartPayloadRead.model_validate(dumped)
    assert restored.service_mode is ServiceMode.DINE_IN
    assert restored.table_no == "A1"
