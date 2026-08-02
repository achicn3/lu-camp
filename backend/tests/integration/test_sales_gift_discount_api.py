"""贈品與臨時折扣的 HTTP 合約（P4-a）。

POS 只透過這幾支端點操作贈品與折扣：明細帶 `line_kind`、折扣以**明細順序索引**指定目標、
金額一律由 `POST /sales/quote` 決定（前端不自算）。這裡釘住的是**邊界形狀與錯誤訊息**——
service 層的定價正確性另有 `test_sales_pricing.py` 與 `test_sales_manual_discount.py`。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import decode_access_token, encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.customerdisplay.schemas import CartUpsertRequest
from app.modules.customerdisplay.service import CustomerDisplayService
from app.modules.inventory.models import CatalogProduct
from app.modules.sales.models import DiscountReason, GiftReason
from app.modules.sales.reasons import (
    DEFAULT_DISCOUNT_REASONS,
    DEFAULT_GIFT_REASONS,
    ensure_default_reasons,
)
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole
from tests.integration.customer_display_helpers import ensure_paired_customer_display


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


async def _seed(session: AsyncSession) -> tuple[str, int, int, int, int]:
    """建店＋店員（開帳）＋兩樣商品＋各一個原因代碼。回 (token, store_id, a, b, gift_reason)。"""
    store = Store(name="贈品折扣店")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="gd", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("2000"))
    a = CatalogProduct(
        store_id=store.id, sku="GD-A", name="甲", unit_price=Decimal("600"),
        unit_cost=Decimal("200"), quantity_on_hand=30,
    )
    b = CatalogProduct(
        store_id=store.id, sku="GD-B", name="乙", unit_price=Decimal("400"),
        unit_cost=Decimal("150"), quantity_on_hand=30,
    )
    gift_reason = GiftReason(store_id=store.id, code="PROMO", name="活動贈品", sort_order=1)
    session.add_all([a, b, gift_reason])
    await session.flush()
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return token, store.id, a.id, b.id, gift_reason.id


def _auth(token: str, *, idem: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return headers


def _line(catalog_id: int, qty: int = 1) -> dict[str, object]:
    return {"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": qty}


# ── 試算 ────────────────────────────────────────────────────────────────────


async def test_quote_applies_item_discount_and_reports_the_breakdown(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """POS 顯示的每一個數字都要能從試算拿到，前端不得自己算折扣。"""
    token, _store_id, a_id, b_id, _gift = await _seed(db_session)
    resp = await client.post(
        "/api/v1/sales/quote",
        json={
            "lines": [_line(a_id), _line(b_id)],
            "adjustments": [
                {
                    "scope": "ITEM",
                    "method": "FIXED_AMOUNT",
                    "value": "100",
                    "target_line_index": 0,
                }
            ],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == "900"
    assert body["item_discount_amount"] == "100"
    assert body["order_discount_amount"] == "0"
    assert body["gift_retail_value"] == "0"
    first, second = body["lines"]
    assert (first["manual_discount_amount"], first["net_amount"]) == ("100", "500")
    assert (second["manual_discount_amount"], second["net_amount"]) == ("0", "400")
    assert first["unit_price"] == "600"  # 牌價不被覆蓋


async def test_quote_reports_gift_value_separately_from_discount(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """贈品價值不得混進折扣：兩者在畫面上要分開顯示，報表也各走各的欄位。"""
    token, _store_id, a_id, b_id, gift_reason = await _seed(db_session)
    resp = await client.post(
        "/api/v1/sales/quote",
        json={
            "lines": [
                _line(a_id),
                {
                    "line_type": "CATALOG",
                    "catalog_product_id": b_id,
                    "qty": 1,
                    "line_kind": "GIFT",
                    "gift_reason_id": gift_reason,
                },
            ]
        },
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == "600"  # 贈品不計入應付
    assert body["gift_retail_value"] == "400"
    assert body["item_discount_amount"] == "0"
    gift_line = body["lines"][1]
    assert gift_line["line_kind"] == "GIFT"
    assert (gift_line["net_amount"], gift_line["discount_amount"]) == ("0", "0")
    assert gift_line["original_unit_price"] == "400"


# ── 結帳 ────────────────────────────────────────────────────────────────────


async def test_checkout_with_order_discount_charges_the_discounted_total(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _store_id, a_id, b_id, _gift = await _seed(db_session)
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [_line(a_id), _line(b_id)],
            "adjustments": [
                {"scope": "ORDER", "method": "PERCENTAGE", "value": "10"}
            ],
            "tenders": [{"tender_type": "CASH", "amount": "900"}],
        },
        headers=_auth(token, idem="gd-order-1"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["total"] == "900"  # 1000 的 10%
    assert [line["net_amount"] for line in body["lines"]] == ["540", "360"]


async def test_checkout_of_a_gift_only_sale_takes_no_payment(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """全贈品單（店主裁示要支援）：總額 0、不帶收款明細。"""
    token, _store_id, _a_id, b_id, gift_reason = await _seed(db_session)
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [
                {
                    "line_type": "CATALOG",
                    "catalog_product_id": b_id,
                    "qty": 2,
                    "line_kind": "GIFT",
                    "gift_reason_id": gift_reason,
                }
            ]
        },
        headers=_auth(token, idem="gd-gift-only"),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["total"] == "0"
    assert body["tenders"] == []


async def test_discount_to_zero_is_rejected_with_a_message_that_says_use_a_gift(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """折到 0 元＝變相贈品。錯誤訊息必須告訴店員該怎麼做，而不是只說「不合法」。"""
    token, _store_id, a_id, _b_id, _gift = await _seed(db_session)
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [_line(a_id)],
            "adjustments": [
                {"scope": "ORDER", "method": "FIXED_AMOUNT", "value": "600"}
            ],
        },
        headers=_auth(token, idem="gd-zero"),
    )
    assert resp.status_code == 422, resp.text
    assert "贈品" in resp.json()["detail"]


# ── 邊界驗證 ────────────────────────────────────────────────────────────────


async def test_item_discount_without_a_target_is_rejected_at_the_boundary(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _store_id, a_id, _b_id, _gift = await _seed(db_session)
    resp = await client.post(
        "/api/v1/sales/quote",
        json={
            "lines": [_line(a_id)],
            "adjustments": [{"scope": "ITEM", "method": "FIXED_AMOUNT", "value": "10"}],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


async def test_discount_target_out_of_range_is_rejected_at_the_boundary(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """越界索引若放進定價層，只會得到「商品不存在」這種看不懂的訊息。"""
    token, _store_id, a_id, _b_id, _gift = await _seed(db_session)
    resp = await client.post(
        "/api/v1/sales/quote",
        json={
            "lines": [_line(a_id)],
            "adjustments": [
                {
                    "scope": "ITEM",
                    "method": "FIXED_AMOUNT",
                    "value": "10",
                    "target_line_index": 5,
                }
            ],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


async def test_hundred_percent_discount_is_rejected_at_the_boundary(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _store_id, a_id, _b_id, _gift = await _seed(db_session)
    resp = await client.post(
        "/api/v1/sales/quote",
        json={
            "lines": [_line(a_id)],
            "adjustments": [
                {"scope": "ORDER", "method": "PERCENTAGE", "value": "100"}
            ],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


# ── 原因代碼選單 ────────────────────────────────────────────────────────────


async def test_reason_menus_return_only_active_reasons_of_this_store(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """停用的原因不再出現在選單，但歷史單據仍留著名稱快照（不實刪）。"""
    token, store_id, _a_id, _b_id, _gift = await _seed(db_session)
    other_store = Store(name="別店")
    db_session.add(other_store)
    await db_session.flush()
    db_session.add_all(
        [
            GiftReason(store_id=store_id, code="OLD", name="已停用", is_active=False),
            GiftReason(store_id=other_store.id, code="PROMO", name="別店的原因"),
            DiscountReason(store_id=store_id, code="DEFECT", name="商品瑕疵", requires_note=True),
            DiscountReason(store_id=other_store.id, code="X", name="別店折扣原因"),
        ]
    )
    await db_session.flush()

    gifts = await client.get("/api/v1/gift-reasons", headers=_auth(token))
    assert gifts.status_code == 200, gifts.text
    assert [r["name"] for r in gifts.json()] == ["活動贈品"]

    discounts = await client.get("/api/v1/discount-reasons", headers=_auth(token))
    assert discounts.status_code == 200, discounts.text
    assert [(r["name"], r["requires_note"]) for r in discounts.json()] == [("商品瑕疵", True)]


async def test_reason_menus_require_authentication(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/gift-reasons")).status_code == 401
    assert (await client.get("/api/v1/discount-reasons")).status_code == 401


# ── 客顯權威購物車 ──────────────────────────────────────────────────────────


async def test_cart_carries_the_discount_and_checkout_still_matches_the_snapshot(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """折扣必須經客顯購物車這條路徑進來。

    客顯是**權威購物車**：結帳時會把快照與實際成交明細逐欄位比對。折扣只送到 `/sales`
    而沒進購物車，客人螢幕上看到的金額就會與實際扣款不同，且比對會整筆擋下結帳。
    """
    token, store_id, a_id, b_id, _gift = await _seed(db_session)
    clerk_id = int(decode_access_token(token)["sub"])
    terminal, _device = await ensure_paired_customer_display(
        db_session, store_id=store_id, actor_user_id=clerk_id
    )
    adjustments = [{"scope": "ORDER", "method": "FIXED_AMOUNT", "value": "100"}]
    cart = await CustomerDisplayService(db_session).upsert_cart(
        store_id,
        terminal.id,
        CartUpsertRequest.model_validate(
            {
                "expected_revision": None,
                "lines": [
                    {"line_type": "CATALOG", "catalog_product_id": a_id, "qty": 1},
                    {"line_type": "CATALOG", "catalog_product_id": b_id, "qty": 1},
                ],
                "adjustments": adjustments,
                "tenders": [{"tender_type": "CASH", "amount": "900"}],
            }
        ),
        actor_user_id=clerk_id,
    )
    assert cart.snapshot["total"] == "900"
    assert cart.snapshot["manual_discount_total"] == "100"
    items = cart.snapshot["items"]
    assert isinstance(items, list)
    assert [item["net_amount"] for item in items] == ["540", "360"]

    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [_line(a_id), _line(b_id)],
            "adjustments": adjustments,
            "tenders": [{"tender_type": "CASH", "amount": "900"}],
            "cart_session_id": cart.id,
            "cart_revision": cart.revision,
        },
        headers=_auth(token, idem="gd-cart-1"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["total"] == "900"


async def test_checkout_without_the_carts_discount_is_refused(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """反向證明：購物車有折扣、結帳沒帶，比對必須擋下（否則客人簽的與扣的會不一致）。"""
    token, store_id, a_id, b_id, _gift = await _seed(db_session)
    clerk_id = int(decode_access_token(token)["sub"])
    terminal, _device = await ensure_paired_customer_display(
        db_session, store_id=store_id, actor_user_id=clerk_id
    )
    cart = await CustomerDisplayService(db_session).upsert_cart(
        store_id,
        terminal.id,
        CartUpsertRequest.model_validate(
            {
                "expected_revision": None,
                "lines": [
                    {"line_type": "CATALOG", "catalog_product_id": a_id, "qty": 1},
                    {"line_type": "CATALOG", "catalog_product_id": b_id, "qty": 1},
                ],
                "adjustments": [{"scope": "ORDER", "method": "FIXED_AMOUNT", "value": "100"}],
                "tenders": [{"tender_type": "CASH", "amount": "900"}],
            }
        ),
        actor_user_id=clerk_id,
    )
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [_line(a_id), _line(b_id)],
            "tenders": [{"tender_type": "CASH", "amount": "1000"}],
            "cart_session_id": cart.id,
            "cart_revision": cart.revision,
        },
        headers=_auth(token, idem="gd-cart-2"),
    )
    assert resp.status_code == 422, resp.text


async def test_a_new_store_gets_default_reasons_so_gifting_is_possible(
    db_session: AsyncSession,
) -> None:
    """沒有原因代碼的門市根本送不出贈品——建店時必須佈建預設值，且重跑不重複。"""
    store = Store(name="新開的店")
    db_session.add(store)
    await db_session.flush()

    first = await ensure_default_reasons(db_session, store.id)
    assert first == len(DEFAULT_GIFT_REASONS) + len(DEFAULT_DISCOUNT_REASONS)
    assert await ensure_default_reasons(db_session, store.id) == 0  # 冪等

    gifts = await SalesService(db_session).list_gift_reasons(store.id)
    assert [r.code for r in gifts] == [code for code, _n, _rn in DEFAULT_GIFT_REASONS]


async def test_default_reasons_do_not_overwrite_a_renamed_reason(
    db_session: AsyncSession,
) -> None:
    """店家改過的名稱／停用狀態不得被重跑蓋掉。"""
    store = Store(name="改過名字的店")
    db_session.add(store)
    await db_session.flush()
    db_session.add(
        GiftReason(store_id=store.id, code="PROMOTION", name="我自己的名字", is_active=False)
    )
    await db_session.flush()

    await ensure_default_reasons(db_session, store.id)

    kept = await db_session.scalar(
        select(GiftReason).where(
            GiftReason.store_id == store.id, GiftReason.code == "PROMOTION"
        )
    )
    assert kept is not None
    assert kept.name == "我自己的名字"
    assert kept.is_active is False


# ── 原因代碼管理（後台） ────────────────────────────────────────────────────


async def test_manager_can_add_a_reason_and_clerks_see_it_immediately(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _a, _b, _gift = await _seed(db_session)
    manager = User(
        store_id=store_id, username="gd-mgr", password_hash="h", role=UserRole.MANAGER
    )
    db_session.add(manager)
    await db_session.flush()
    mgr_token = encode_access_token(user_id=manager.id, role="MANAGER", store_id=store_id)

    created = await client.post(
        "/api/v1/gift-reasons",
        json={"code": "SAMPLE", "name": "試用品", "requires_note": True, "sort_order": 5},
        headers=_auth(mgr_token),
    )
    assert created.status_code == 201, created.text
    assert created.json()["requires_note"] is True

    menu = await client.get("/api/v1/gift-reasons", headers=_auth(token))
    assert "試用品" in [r["name"] for r in menu.json()]


async def test_duplicate_reason_code_is_refused_with_a_useful_message(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """停用的也算佔用 code——直接說「請改為重新啟用」，不然店員會困惑。"""
    _token, store_id, _a, _b, _gift = await _seed(db_session)
    manager = User(
        store_id=store_id, username="gd-mgr2", password_hash="h", role=UserRole.MANAGER
    )
    db_session.add(manager)
    await db_session.flush()
    mgr_token = encode_access_token(user_id=manager.id, role="MANAGER", store_id=store_id)

    duplicate = await client.post(
        "/api/v1/gift-reasons",
        json={"code": "PROMO", "name": "重複的", "requires_note": False, "sort_order": 0},
        headers=_auth(mgr_token),
    )
    assert duplicate.status_code == 409, duplicate.text
    assert "重新啟用" in duplicate.json()["detail"]


async def test_disabling_a_reason_hides_it_from_pos_but_keeps_it_for_history(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """停用不實刪：POS 選單看不到，管理頁仍列得出來（歷史單據還引用著）。"""
    token, store_id, _a, _b, gift_reason = await _seed(db_session)
    manager = User(
        store_id=store_id, username="gd-mgr3", password_hash="h", role=UserRole.MANAGER
    )
    db_session.add(manager)
    await db_session.flush()
    mgr_token = encode_access_token(user_id=manager.id, role="MANAGER", store_id=store_id)

    disabled = await client.patch(
        f"/api/v1/gift-reasons/{gift_reason}",
        json={"is_active": False},
        headers=_auth(mgr_token),
    )
    assert disabled.status_code == 200, disabled.text

    pos_menu = await client.get("/api/v1/gift-reasons", headers=_auth(token))
    assert pos_menu.json() == []
    admin_list = await client.get(
        "/api/v1/gift-reasons?include_inactive=true", headers=_auth(mgr_token)
    )
    assert [(r["name"], r["is_active"]) for r in admin_list.json()] == [("活動贈品", False)]


async def test_clerks_may_not_manage_reasons(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _store_id, _a, _b, gift_reason = await _seed(db_session)
    created = await client.post(
        "/api/v1/gift-reasons",
        json={"code": "NOPE", "name": "不該成功", "requires_note": False, "sort_order": 0},
        headers=_auth(token),
    )
    assert created.status_code == 403, created.text
    patched = await client.patch(
        f"/api/v1/gift-reasons/{gift_reason}",
        json={"name": "不該成功"},
        headers=_auth(token),
    )
    assert patched.status_code == 403, patched.text


async def test_updating_a_missing_reason_is_a_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _token, store_id, _a, _b, _gift = await _seed(db_session)
    manager = User(
        store_id=store_id, username="gd-mgr4", password_hash="h", role=UserRole.MANAGER
    )
    db_session.add(manager)
    await db_session.flush()
    mgr_token = encode_access_token(user_id=manager.id, role="MANAGER", store_id=store_id)
    missing = await client.patch(
        "/api/v1/discount-reasons/999999",
        json={"name": "不存在"},
        headers=_auth(mgr_token),
    )
    assert missing.status_code == 404, missing.text
