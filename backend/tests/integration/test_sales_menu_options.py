"""sales × 彈性菜單（docs/44 §3.2–3.3）：結帳帶選項、後端計價、選項寫進品名與快照。

- 單價 = 品項基本價 + Σ 所選選項加價；永遠由後端依菜單計算。
- 選項合法性由後端擋（必選、數量上限、選項屬於品項所掛群組、停售）。
- 同一品項不同選項是不同明細，品名帶出選項，收據／出餐單／發票／客顯不必另改。
- 冪等指紋：沒帶選項時維持舊形狀（部署前送出的重送不被誤判）。
"""

import hashlib
import json
from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.menu.models import MenuItem, MenuOption
from app.modules.menu.service import MenuService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.models import SaleLine
from app.modules.sales.service import _line_fingerprint
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import SaleLineType, UserRole
from tests.integration.customer_display_helpers import CustomerDisplayAwareClient


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


class _Menu:
    """拿鐵 150：溫度（必選單選：熱/冰）＋奶（必選單選：鮮奶/燕麥奶 +20）
    ＋加購（可選 0–2：濃縮 +30、香草 +15）。"""

    def __init__(self) -> None:
        self.store_id = 0
        self.token = ""
        self.latte = 0
        self.clerk_id = 0
        self.opt: dict[str, int] = {}


async def _seed(session: AsyncSession) -> _Menu:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("1000"))
    svc = MenuService(session)
    latte = await svc.create_menu_item(
        store.id, name="拿鐵", unit_price=Decimal(150), actor_user_id=clerk.id
    )
    temp = await svc.create_option_group(
        store.id,
        name="溫度",
        min_select=1,
        max_select=1,
        options=[("熱", Decimal(0)), ("冰", Decimal(0))],
        actor_user_id=clerk.id,
    )
    milk = await svc.create_option_group(
        store.id,
        name="奶",
        min_select=1,
        max_select=1,
        options=[("鮮奶", Decimal(0)), ("燕麥奶", Decimal(20))],
        actor_user_id=clerk.id,
    )
    extra = await svc.create_option_group(
        store.id,
        name="加購",
        min_select=0,
        max_select=2,
        options=[("濃縮", Decimal(30)), ("香草", Decimal(15))],
        actor_user_id=clerk.id,
    )
    await svc.set_item_option_groups(
        store.id, latte.id, [temp.group.id, milk.group.id, extra.group.id], actor_user_id=clerk.id
    )
    m = _Menu()
    m.store_id = store.id
    m.token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    m.latte = latte.id
    m.clerk_id = clerk.id
    for detail in (temp, milk, extra):
        for o in detail.options:
            m.opt[o.name] = o.id
    return m


def _headers(m: _Menu, idem: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {m.token}", "Idempotency-Key": idem}


def _line(item_id: int, option_ids: list[int], qty: int = 1) -> dict[str, object]:
    return {"line_type": "MENU", "menu_item_id": item_id, "qty": qty, "menu_option_ids": option_ids}


async def _sell(
    client: httpx.AsyncClient, m: _Menu, lines: list[dict[str, object]], idem: str
) -> httpx.Response:
    return await client.post(
        "/api/v1/sales",
        json={"lines": lines, "service_mode": "TAKEOUT"},
        headers=_headers(m, idem),
    )


async def test_options_price_and_name_the_line(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _seed(db_session)
    resp = await _sell(client, m, [_line(m.latte, [m.opt["冰"], m.opt["燕麥奶"]], qty=2)], "o1")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["total"] == "340"  # (150 + 0 + 20) × 2
    line = body["lines"][0]
    assert line["unit_price"] == "170"
    assert line["description"] == "拿鐵（冰、燕麥奶）"

    row = await db_session.scalar(select(SaleLine).where(SaleLine.sale_id == body["id"]))
    assert row is not None
    snap = row.menu_options_snapshot
    assert snap is not None
    assert snap == [
        {
            "group_id": snap[0]["group_id"],
            "group": "溫度",
            "option_id": m.opt["冰"],
            "option": "冰",
            "price_delta": "0",
        },
        {
            "group_id": snap[1]["group_id"],
            "group": "奶",
            "option_id": m.opt["燕麥奶"],
            "option": "燕麥奶",
            "price_delta": "20",
        },
    ]


async def test_option_order_from_client_does_not_matter(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """品名依群組、選項的設定順序排，不依客戶端送來的順序。"""
    m = await _seed(db_session)
    resp = await _sell(
        client,
        m,
        [_line(m.latte, [m.opt["香草"], m.opt["鮮奶"], m.opt["熱"], m.opt["濃縮"]])],
        "o2",
    )
    assert resp.status_code == 201, resp.text
    line = resp.json()["lines"][0]
    assert line["description"] == "拿鐵（熱、鮮奶、濃縮、香草）"
    assert line["unit_price"] == "195"  # 150 + 30 + 15


async def test_same_item_different_options_are_separate_lines(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _seed(db_session)
    resp = await _sell(
        client,
        m,
        [
            _line(m.latte, [m.opt["熱"], m.opt["鮮奶"]]),
            _line(m.latte, [m.opt["冰"], m.opt["燕麥奶"]]),
        ],
        "o3",
    )
    assert resp.status_code == 201, resp.text
    assert [ln["description"] for ln in resp.json()["lines"]] == [
        "拿鐵（熱、鮮奶）",
        "拿鐵（冰、燕麥奶）",
    ]
    assert resp.json()["total"] == "320"


async def test_missing_required_group_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _seed(db_session)
    resp = await _sell(client, m, [_line(m.latte, [m.opt["冰"]])], "o4")
    assert resp.status_code == 422, resp.text
    assert "奶" in resp.json()["detail"]


async def test_too_many_in_single_select_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _seed(db_session)
    resp = await _sell(client, m, [_line(m.latte, [m.opt["冰"], m.opt["熱"], m.opt["鮮奶"]])], "o5")
    assert resp.status_code == 422, resp.text
    assert "溫度" in resp.json()["detail"]


async def test_duplicate_option_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _seed(db_session)
    resp = await _sell(
        client,
        m,
        [_line(m.latte, [m.opt["冰"], m.opt["鮮奶"], m.opt["濃縮"], m.opt["濃縮"]])],
        "o6",
    )
    assert resp.status_code == 422, resp.text


async def test_option_from_unattached_group_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _seed(db_session)
    americano = MenuItem(store_id=m.store_id, name="美式", unit_price=Decimal(120))
    db_session.add(americano)
    await db_session.flush()
    resp = await _sell(client, m, [_line(americano.id, [m.opt["燕麥奶"]])], "o7")
    assert resp.status_code == 422, resp.text


async def test_unavailable_option_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _seed(db_session)
    oat = await db_session.get(MenuOption, m.opt["燕麥奶"])
    assert oat is not None
    oat.is_available = False
    await db_session.flush()
    resp = await _sell(client, m, [_line(m.latte, [m.opt["冰"], m.opt["燕麥奶"]])], "o8")
    assert resp.status_code == 409, resp.text
    assert "燕麥奶" in resp.json()["detail"]


async def test_item_without_groups_still_sells_without_options(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """既有扁平品項（沒掛群組）照舊：不帶選項即可賣，品名不加括號。"""
    m = await _seed(db_session)
    water = MenuItem(store_id=m.store_id, name="白開水", unit_price=Decimal(10))
    db_session.add(water)
    await db_session.flush()
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [{"line_type": "MENU", "menu_item_id": water.id, "qty": 1}],
            "service_mode": "TAKEOUT",
        },
        headers=_headers(m, "o9"),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["lines"][0]["description"] == "白開水"


async def test_options_only_allowed_on_menu_lines(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _seed(db_session)
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [
                {"line_type": "CATALOG", "catalog_product_id": 1, "qty": 1, "menu_option_ids": [1]}
            ],
            "service_mode": "TAKEOUT",
        },
        headers=_headers(m, "o10"),
    )
    assert resp.status_code == 422
    assert "只有餐飲明細可以帶選項" in resp.text  # 不是因為商品不存在才擋


async def test_quote_prices_options(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    m = await _seed(db_session)
    resp = await client.post(
        "/api/v1/sales/quote",
        json={"lines": [_line(m.latte, [m.opt["冰"], m.opt["燕麥奶"], m.opt["濃縮"]], qty=2)]},
        headers={"Authorization": f"Bearer {m.token}"},
    )
    assert resp.status_code == 200, resp.text
    line = resp.json()["lines"][0]
    assert line["unit_price"] == "200"
    assert line["description"] == "拿鐵（冰、燕麥奶、濃縮）"
    assert resp.json()["total"] == "400"


async def test_idempotent_replay_with_options_and_conflict_on_different_options(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _seed(db_session)
    lines = [_line(m.latte, [m.opt["冰"], m.opt["鮮奶"]])]
    first = await _sell(client, m, lines, "dup")
    assert first.status_code == 201, first.text
    again = await _sell(client, m, [_line(m.latte, [m.opt["鮮奶"], m.opt["冰"]])], "dup")
    assert again.json()["id"] == first.json()["id"]  # 同選項、不同順序＝同一請求
    other = await _sell(client, m, [_line(m.latte, [m.opt["熱"], m.opt["鮮奶"]])], "dup")
    assert other.status_code == 409


def test_line_fingerprint_without_options_keeps_old_shape() -> None:
    """沒帶選項的餐飲行，指紋與加欄位前完全相同（部署前送出的重送不會被當成不同請求）。"""
    line = SaleLineInput(line_type=SaleLineType.MENU, menu_item_id=7, qty=2)
    old_shape = {
        "line_type": "MENU",
        "item_code": None,
        "catalog_product_id": None,
        "bulk_lot_id": None,
        "menu_item_id": 7,
        "qty": 2,
        "line_kind": "NORMAL",
        "gift_reason_id": None,
        "gift_note": None,
    }
    assert _line_fingerprint(line) == old_shape
    digest = hashlib.sha256(json.dumps(_line_fingerprint(line), sort_keys=True).encode())
    assert (
        digest.hexdigest()
        == hashlib.sha256(json.dumps(old_shape, sort_keys=True).encode()).hexdigest()
    )


def test_line_fingerprint_with_options_is_order_independent() -> None:
    a = SaleLineInput(line_type=SaleLineType.MENU, menu_item_id=7, menu_option_ids=(3, 1))
    b = SaleLineInput(line_type=SaleLineType.MENU, menu_item_id=7, menu_option_ids=(1, 3))
    assert _line_fingerprint(a) == _line_fingerprint(b)
    assert _line_fingerprint(a)["menu_option_ids"] == [1, 3]


async def test_same_option_name_in_two_groups_is_disambiguated(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """甜度、冰量都有「正常」：只在撞名時把群組名稱帶進品名，其餘維持短格式。"""
    m = await _seed(db_session)
    svc = MenuService(db_session)
    tea = await svc.create_menu_item(
        m.store_id, name="綠茶", unit_price=Decimal(50), actor_user_id=m.clerk_id
    )
    sugar = await svc.create_option_group(
        m.store_id,
        name="甜度",
        min_select=1,
        max_select=1,
        options=[("正常", Decimal(0)), ("微糖", Decimal(0))],
        actor_user_id=m.clerk_id,
    )
    ice = await svc.create_option_group(
        m.store_id,
        name="冰量",
        min_select=1,
        max_select=1,
        options=[("正常", Decimal(0)), ("少冰", Decimal(0))],
        actor_user_id=m.clerk_id,
    )
    await svc.set_item_option_groups(
        m.store_id, tea.id, [sugar.group.id, ice.group.id], actor_user_id=m.clerk_id
    )
    normal_sugar = sugar.options[0].id
    normal_ice, less_ice = ice.options[0].id, ice.options[1].id

    resp = await _sell(
        client,
        m,
        [_line(tea.id, [normal_sugar, normal_ice]), _line(tea.id, [normal_sugar, less_ice])],
        "dup-name",
    )
    assert resp.status_code == 201, resp.text
    assert [ln["description"] for ln in resp.json()["lines"]] == [
        "綠茶（甜度正常、冰量正常）",
        "綠茶（正常、少冰）",
    ]
    snap = resp.json()["lines"][0]["menu_options_snapshot"]
    assert [(o["group"], o["option"]) for o in snap] == [("甜度", "正常"), ("冰量", "正常")]
