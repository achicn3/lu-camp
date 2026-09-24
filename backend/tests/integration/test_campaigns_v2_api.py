"""門市活動 v2 管理 API（docs/40 P1，2026-09-23）：可疊加開關、範圍條件（包含／排除）。

範圍可細到分類／品牌／型號／單件／一般商品／販售籃；一律須屬本店，他店或不存在的 id → 422。
讀回時附名稱（label），管理頁不必再逐一查。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.inventory.models import Brand, Category, ProductModel, SerializedItem
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import Grade, OwnershipType, SerializedItemStatus, UserRole

PATH = "/api/v1/campaigns"


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


async def _store(db: AsyncSession, name: str = "門市") -> tuple[int, str]:
    store = Store(name=name)
    db.add(store)
    await db.flush()
    mgr = User(
        store_id=store.id, username=f"mgr{store.id}", password_hash="h", role=UserRole.MANAGER
    )
    db.add(mgr)
    await db.flush()
    return store.id, encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "露營週",
        "discount_pct": 10,
        "starts_at": "2026-06-01T00:00:00Z",
        "ends_at": "2026-07-01T00:00:00Z",
    }
    base.update(overrides)
    return base


async def test_defaults_not_stackable_and_no_targets(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, mgr = await _store(db_session)
    resp = await client.post(PATH, json=_payload(), headers=_auth(mgr))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["stackable"] is False
    assert body["targets"] == []


async def test_create_with_stackable_and_targets_reads_back_with_labels(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, mgr = await _store(db_session)
    category = Category(store_id=store_id, name="帳篷", target_margin_pct=45)
    brand = Brand(store_id=store_id, name="Snow Peak")
    db_session.add_all([category, brand])
    await db_session.flush()
    model = ProductModel(store_id=store_id, brand_id=brand.id, name="Amenity Dome")
    db_session.add(model)
    await db_session.flush()
    item = SerializedItem(
        store_id=store_id,
        item_code="ITM-V2-1",
        name="Amenity Dome M",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal(8000),
        status=SerializedItemStatus.IN_STOCK,
    )
    db_session.add(item)
    await db_session.flush()

    resp = await client.post(
        PATH,
        json=_payload(
            stackable=True,
            targets=[
                {"mode": "INCLUDE", "target_type": "CATEGORY", "target_id": category.id},
                {"mode": "INCLUDE", "target_type": "PRODUCT_MODEL", "target_id": model.id},
                {"mode": "INCLUDE", "target_type": "PRODUCT_MODEL", "target_id": model.id},
                {"mode": "EXCLUDE", "target_type": "SERIALIZED_ITEM", "target_id": item.id},
            ],
        ),
        headers=_auth(mgr),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["stackable"] is True
    targets = {(t["mode"], t["target_type"], t["target_id"]): t["label"] for t in body["targets"]}
    assert targets == {
        ("INCLUDE", "CATEGORY", category.id): "帳篷",
        ("INCLUDE", "PRODUCT_MODEL", model.id): "Snow Peak Amenity Dome",
        ("EXCLUDE", "SERIALIZED_ITEM", item.id): "Amenity Dome M（ITM-V2-1）",
    }

    got = await client.get(f"{PATH}/{body['id']}", headers=_auth(mgr))
    assert len(got.json()["targets"]) == 3


async def test_target_from_another_store_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _mine, mgr = await _store(db_session)
    other, _ = await _store(db_session, "他店")
    foreign = Category(store_id=other, name="他店分類", target_margin_pct=45)
    db_session.add(foreign)
    await db_session.flush()

    resp = await client.post(
        PATH,
        json=_payload(
            targets=[{"mode": "INCLUDE", "target_type": "CATEGORY", "target_id": foreign.id}]
        ),
        headers=_auth(mgr),
    )
    assert resp.status_code == 422, resp.text


async def test_missing_target_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _mine, mgr = await _store(db_session)
    resp = await client.post(
        PATH,
        json=_payload(
            targets=[{"mode": "EXCLUDE", "target_type": "BULK_BASKET", "target_id": 999999}]
        ),
        headers=_auth(mgr),
    )
    assert resp.status_code == 422, resp.text


# ── P2：指定特價、每件折金額 ────────────────────────────────────────


async def test_create_fixed_price_and_amount_off_campaigns(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, mgr = await _store(db_session)
    fixed = await client.post(
        PATH,
        json={
            "name": "營燈特價",
            "kind": "FIXED_PRICE",
            "fixed_price": "690",
            "starts_at": "2026-06-01T00:00:00Z",
            "ends_at": "2026-07-01T00:00:00Z",
        },
        headers=_auth(mgr),
    )
    assert fixed.status_code == 201, fixed.text
    body = fixed.json()
    assert (body["kind"], body["fixed_price"], body["discount_pct"]) == ("FIXED_PRICE", "690", None)

    off = await client.post(
        PATH,
        json={
            "name": "每件折 100",
            "kind": "AMOUNT_OFF",
            "amount_off": "100",
            "starts_at": "2026-06-01T00:00:00Z",
            "ends_at": "2026-07-01T00:00:00Z",
        },
        headers=_auth(mgr),
    )
    assert off.status_code == 201, off.text
    assert (off.json()["kind"], off.json()["amount_off"]) == ("AMOUNT_OFF", "100")


async def test_old_clients_still_create_percent_campaigns(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, mgr = await _store(db_session)
    resp = await client.post(PATH, json=_payload(), headers=_auth(mgr))
    assert resp.status_code == 201
    assert resp.json()["kind"] == "PERCENT_OFF"
    assert resp.json()["discount_pct"] == 10


async def test_kind_and_value_must_match(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, mgr = await _store(db_session)
    bad: list[dict[str, str | int]] = [
        {"kind": "FIXED_PRICE"},  # 沒給特價
        {"kind": "FIXED_PRICE", "fixed_price": "0"},
        {"kind": "AMOUNT_OFF", "amount_off": "-5"},
        {"kind": "PERCENT_OFF"},  # 沒給折扣
        {"kind": "PERCENT_OFF", "discount_pct": 10, "fixed_price": "500"},  # 多給別種的值
    ]
    for extra in bad:
        payload: dict[str, str | int] = {
            "name": "錯的",
            "starts_at": "2026-06-01T00:00:00Z",
            "ends_at": "2026-07-01T00:00:00Z",
            **extra,
        }
        resp = await client.post(PATH, json=payload, headers=_auth(mgr))
        assert resp.status_code == 422, (extra, resp.text)


# ── P3：買 N 送 M ───────────────────────────────────────────────────


def _bngm(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "瓦斯罐買五送一",
        "kind": "BUY_N_GET_M",
        "buy_qty": 5,
        "free_qty": 1,
        "applies_catalog": True,
        "starts_at": "2026-06-01T00:00:00Z",
        "ends_at": "2026-07-01T00:00:00Z",
    }
    base.update(overrides)
    return base


async def test_create_buy_n_get_m_campaign(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, mgr = await _store(db_session)
    resp = await client.post(PATH, json=_bngm(), headers=_auth(mgr))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["kind"], body["buy_qty"], body["free_qty"], body["discount_pct"]) == (
        "BUY_N_GET_M",
        5,
        1,
        None,
    )
    assert body["applies_consignment"] is False


async def test_buy_n_get_m_values_must_be_valid(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, mgr = await _store(db_session)
    bad: list[dict[str, object]] = [
        {"free_qty": None},  # 沒給送幾件
        {"buy_qty": 0},
        {"free_qty": 100},
        {"discount_pct": 10},  # 多給別種的值
        {"applies_consignment": True},  # 寄售品不能參加買 N 送 M（裁示 7）
    ]
    for extra in bad:
        resp = await client.post(PATH, json=_bngm(**extra), headers=_auth(mgr))
        assert resp.status_code == 422, (extra, resp.text)
    # 別種活動不可帶 buy_qty／free_qty
    resp = await client.post(PATH, json=_payload(buy_qty=2), headers=_auth(mgr))
    assert resp.status_code == 422, resp.text


# ── P4：組合價 ───────────────────────────────────────────────────────


async def _two_models(db: AsyncSession, store_id: int) -> tuple[int, int]:
    brand = Brand(store_id=store_id, name="Snow Peak")
    db.add(brand)
    await db.flush()
    tent = ProductModel(store_id=store_id, brand_id=brand.id, name="Amenity Dome")
    chair = ProductModel(store_id=store_id, brand_id=brand.id, name="Low Chair")
    db.add_all([tent, chair])
    await db.flush()
    return tent.id, chair.id


def _bundle(tent: int, chair: int, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "帳篷＋椅子組合",
        "kind": "BUNDLE",
        "bundle_price": "7000",
        "bundle_slots": [
            {"qty": 1, "targets": [{"target_type": "PRODUCT_MODEL", "target_id": tent}]},
            {"qty": 2, "targets": [{"target_type": "PRODUCT_MODEL", "target_id": chair}]},
        ],
        "starts_at": "2026-06-01T00:00:00Z",
        "ends_at": "2026-07-01T00:00:00Z",
    }
    base.update(overrides)
    return base


async def test_create_bundle_reads_back_slots_with_labels(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, mgr = await _store(db_session)
    tent, chair = await _two_models(db_session, store_id)
    resp = await client.post(PATH, json=_bundle(tent, chair), headers=_auth(mgr))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["kind"], body["bundle_price"]) == ("BUNDLE", "7000")
    assert [
        (s["slot_no"], s["qty"], [t["label"] for t in s["targets"]]) for s in body["bundle_slots"]
    ] == [(1, 1, ["Snow Peak Amenity Dome"]), (2, 2, ["Snow Peak Low Chair"])]
    got = await client.get(f"{PATH}/{body['id']}", headers=_auth(mgr))
    assert len(got.json()["bundle_slots"]) == 2


async def test_bundle_values_must_be_valid(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, mgr = await _store(db_session)
    tent, chair = await _two_models(db_session, store_id)
    one_slot = [{"qty": 1, "targets": [{"target_type": "PRODUCT_MODEL", "target_id": tent}]}]
    bad: list[dict[str, object]] = [
        {"bundle_price": None},
        {"bundle_slots": one_slot},  # 至少兩格
        {"bundle_slots": [{"qty": 1, "targets": []}, *one_slot]},  # 每格要有範圍
        {"bundle_price": "2"},  # 組合價低於件數（每件至少 1 元）
        {"applies_consignment": True},  # 寄售不進組合包
        {"discount_pct": 10},
    ]
    for extra in bad:
        resp = await client.post(PATH, json=_bundle(tent, chair, **extra), headers=_auth(mgr))
        assert resp.status_code == 422, (extra, resp.text)
    # 別種活動不可帶組合格子
    resp = await client.post(PATH, json=_payload(bundle_slots=one_slot), headers=_auth(mgr))
    assert resp.status_code == 422, resp.text


async def test_bundle_target_from_another_store_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, mgr = await _store(db_session)
    tent, chair = await _two_models(db_session, store_id)
    slots = [
        {"qty": 1, "targets": [{"target_type": "PRODUCT_MODEL", "target_id": 999_999}]},
        {"qty": 1, "targets": [{"target_type": "PRODUCT_MODEL", "target_id": chair}]},
    ]
    resp = await client.post(
        PATH, json=_bundle(tent, chair, bundle_slots=slots), headers=_auth(mgr)
    )
    assert resp.status_code == 422, resp.text
