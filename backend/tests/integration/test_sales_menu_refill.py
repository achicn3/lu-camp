"""sales × 續點（店主 2026-10-16）：勾了「可續點」的品項，客人可以點續點、照折數算價。

- 續點價 = round_ntd((基本價 + Σ 選項加價) × 折數 / 100)，一杯一杯算；加料也打折（裁示 1）。
- 折數放設定 `menu_refill_pct`（預設 80＝八折），不寫死。
- 勾了就算（裁示 3）：後端只檢查品項可不可續點，是不是真的續點由店員現場判斷。
- 品名冠「續點・」：收據、出餐單、發票、客顯直接看得到；續點與一般點是不同明細。
- 冪等指紋：沒續點時維持舊形狀（部署前送出的重送不被誤判）。
- 外帶不能續點（店主 2026-10-19）；POS 查「本桌 90 分鐘內點過的品項」做提醒、不擋。
"""

from collections.abc import AsyncGenerator
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.core.time import utc_now
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.menu.service import MenuService
from app.modules.sales.inputs import SaleLineInput, menu_line_key
from app.modules.sales.models import Sale
from app.modules.sales.service import _line_fingerprint
from app.modules.settings.schemas import SettingsUpdateRequest
from app.modules.settings.service import StoreSettingsService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import SaleLineType, SaleStatus, UserRole
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
    """拿鐵 150（可續點）：奶（必選：鮮奶／燕麥奶 +20）；蛋糕 120（不可續點）。"""

    def __init__(self) -> None:
        self.store_id = 0
        self.manager_id = 0
        self.token = ""
        self.latte = 0
        self.cake = 0
        self.milk = 0
        self.oat = 0


async def _seed(session: AsyncSession) -> _Menu:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    manager = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    session.add(manager)
    await session.flush()
    await CashDrawerService(session).open_session(store.id, manager.id, Decimal("1000"))
    await StoreSettingsService(session).update_settings(
        store.id, actor_user_id=None, patch=SettingsUpdateRequest(dine_in_tables=["A1", "B2"])
    )
    svc = MenuService(session)
    latte = await svc.create_menu_item(
        store.id, name="拿鐵", unit_price=Decimal(150), refillable=True, actor_user_id=manager.id
    )
    cake = await svc.create_menu_item(
        store.id, name="蛋糕", unit_price=Decimal(120), actor_user_id=manager.id
    )
    milk = await svc.create_option_group(
        store.id,
        name="奶",
        min_select=1,
        max_select=1,
        options=[("鮮奶", Decimal(0)), ("燕麥奶", Decimal(20))],
        actor_user_id=manager.id,
    )
    await svc.set_item_option_groups(store.id, latte.id, [milk.group.id], actor_user_id=manager.id)
    m = _Menu()
    m.store_id = store.id
    m.manager_id = manager.id
    m.token = encode_access_token(user_id=manager.id, role="MANAGER", store_id=store.id)
    m.latte = latte.id
    m.cake = cake.id
    m.milk = milk.options[0].id
    m.oat = milk.options[1].id
    return m


def _line(item_id: int, option_ids: list[int], *, refill: bool, qty: int = 1) -> dict[str, object]:
    line: dict[str, object] = {
        "line_type": "MENU",
        "menu_item_id": item_id,
        "qty": qty,
        "menu_option_ids": option_ids,
    }
    if refill:
        line["menu_refill"] = True
    return line


async def _sell(
    client: httpx.AsyncClient,
    m: _Menu,
    lines: list[dict[str, object]],
    idem: str,
    *,
    table_no: str | None = "A1",
) -> httpx.Response:
    """續點限內用：預設內用 A1；`table_no=None` 為外帶。"""
    mode = (
        {"service_mode": "TAKEOUT"}
        if table_no is None
        else {
            "service_mode": "DINE_IN",
            "table_no": table_no,
        }
    )
    return await client.post(
        "/api/v1/sales",
        json={"lines": lines, **mode},
        headers={"Authorization": f"Bearer {m.token}", "Idempotency-Key": idem},
    )


async def test_refill_is_priced_at_refill_pct_including_options(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _seed(db_session)
    resp = await _sell(client, m, [_line(m.latte, [m.oat], refill=True, qty=2)], "r1")
    assert resp.status_code == 201, resp.text
    line = resp.json()["lines"][0]
    assert line["unit_price"] == "136"  # (150 + 20) × 80%：加料也打折
    assert line["description"] == "續點・拿鐵（燕麥奶）"
    assert resp.json()["total"] == "272"


async def test_refill_and_regular_cup_are_separate_lines(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _seed(db_session)
    resp = await _sell(
        client,
        m,
        [_line(m.latte, [m.milk], refill=False), _line(m.latte, [m.milk], refill=True)],
        "r2",
    )
    assert resp.status_code == 201, resp.text
    assert [(ln["description"], ln["unit_price"]) for ln in resp.json()["lines"]] == [
        ("拿鐵（鮮奶）", "150"),
        ("續點・拿鐵（鮮奶）", "120"),
    ]


async def test_refill_on_item_that_is_not_refillable_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _seed(db_session)
    resp = await _sell(client, m, [_line(m.cake, [], refill=True)], "r3")
    assert resp.status_code == 422, resp.text
    assert "續點" in resp.json()["detail"]


async def test_refill_pct_comes_from_settings_and_rounds_half_up(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _seed(db_session)
    await StoreSettingsService(db_session).update_settings(
        m.store_id, actor_user_id=m.manager_id, patch=SettingsUpdateRequest(menu_refill_pct=85)
    )
    water = await MenuService(db_session).create_menu_item(
        m.store_id,
        name="冷泡茶",
        unit_price=Decimal(10),
        refillable=True,
        actor_user_id=m.manager_id,
    )
    resp = await _sell(client, m, [_line(water.id, [], refill=True)], "r4")
    assert resp.status_code == 201, resp.text
    assert resp.json()["lines"][0]["unit_price"] == "9"  # 10 × 85% = 8.5 → 四捨五入 9


async def test_quote_prices_refill(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    m = await _seed(db_session)
    resp = await client.post(
        "/api/v1/sales/quote",
        json={"lines": [_line(m.latte, [m.milk], refill=True, qty=3)]},
        headers={"Authorization": f"Bearer {m.token}"},
    )
    assert resp.status_code == 200, resp.text
    line = resp.json()["lines"][0]
    assert (line["description"], line["unit_price"]) == ("續點・拿鐵（鮮奶）", "120")
    assert resp.json()["total"] == "360"


async def test_idempotent_replay_conflicts_when_refill_flag_changes(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _seed(db_session)
    first = await _sell(client, m, [_line(m.latte, [m.milk], refill=True)], "dup")
    assert first.status_code == 201, first.text
    again = await _sell(client, m, [_line(m.latte, [m.milk], refill=True)], "dup")
    assert again.json()["id"] == first.json()["id"]
    other = await _sell(client, m, [_line(m.latte, [m.milk], refill=False)], "dup")
    assert other.status_code == 409


def test_fingerprint_and_cart_key_keep_old_shape_without_refill() -> None:
    plain = SaleLineInput(line_type=SaleLineType.MENU, menu_item_id=7, menu_option_ids=(1,))
    refill = SaleLineInput(
        line_type=SaleLineType.MENU, menu_item_id=7, menu_option_ids=(1,), menu_refill=True
    )
    assert "menu_refill" not in _line_fingerprint(plain)
    assert _line_fingerprint(refill)["menu_refill"] is True
    assert menu_line_key(plain) == "MENU:7:1"
    assert menu_line_key(refill) == "MENU:7:1:refill"
    bare = SaleLineInput(line_type=SaleLineType.MENU, menu_item_id=7, menu_refill=True)
    assert menu_line_key(bare) == "MENU:7:refill"


async def test_refill_only_allowed_on_menu_lines(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _seed(db_session)
    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [
                {"line_type": "CATALOG", "catalog_product_id": 1, "qty": 1, "menu_refill": True}
            ],
            "service_mode": "TAKEOUT",
        },
        headers={"Authorization": f"Bearer {m.token}", "Idempotency-Key": "r5"},
    )
    assert resp.status_code == 422
    assert "只有餐飲明細可以續點" in resp.text


async def test_menu_item_api_reads_and_toggles_refillable(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _seed(db_session)
    headers = {"Authorization": f"Bearer {m.token}"}
    created = await client.post(
        "/api/v1/menu-items",
        json={"name": "美式", "unit_price": "120", "refillable": True},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["refillable"] is True
    patched = await client.patch(
        f"/api/v1/menu-items/{m.cake}", json={"refillable": True}, headers=headers
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["refillable"] is True
    untouched = await client.patch(
        f"/api/v1/menu-items/{m.cake}", json={"name": "起司蛋糕"}, headers=headers
    )
    assert untouched.json()["refillable"] is True


async def test_settings_expose_refill_pct_with_default_eighty(db_session: AsyncSession) -> None:
    m = await _seed(db_session)
    settings = await StoreSettingsService(db_session).get_effective_settings(m.store_id)
    assert settings.menu_refill_pct == 80


@pytest.mark.parametrize("pct", [0, 100, -5])
def test_refill_pct_must_be_between_1_and_99(pct: int) -> None:
    with pytest.raises(ValidationError):
        SettingsUpdateRequest(menu_refill_pct=pct)


async def test_takeout_cannot_refill(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """外帶不能續點（店主 2026-10-19）。"""
    m = await _seed(db_session)
    resp = await _sell(client, m, [_line(m.latte, [m.milk], refill=True)], "t1", table_no=None)
    assert resp.status_code == 422
    assert "外帶不能續點" in resp.text


async def _recent(client: httpx.AsyncClient, m: _Menu, table_no: str) -> list[int]:
    resp = await client.get(
        "/api/v1/sales/dine-in-recent-items",
        params={"table_no": table_no},
        headers={"Authorization": f"Bearer {m.token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["window_minutes"] == 90
    return sorted(body["menu_item_ids"])


async def test_recent_items_lists_this_tables_menu_items_within_90_minutes(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """POS 勾續點時用來提醒「本桌 90 分鐘內沒點過這項」：只看同一桌的內用、沒作廢、90 分鐘內。"""
    m = await _seed(db_session)
    assert await _recent(client, m, "A1") == []
    first = await _sell(client, m, [_line(m.latte, [m.milk], refill=False)], "n1")
    assert first.status_code == 201, first.text
    other_table = await _sell(client, m, [_line(m.cake, [], refill=False)], "n2", table_no="B2")
    assert other_table.status_code == 201, other_table.text
    takeout = await _sell(client, m, [_line(m.cake, [], refill=False)], "n3", table_no=None)
    assert takeout.status_code == 201, takeout.text
    assert await _recent(client, m, "A1") == [m.latte]
    assert await _recent(client, m, " B2 ") == [m.cake]

    # 超過 90 分鐘就不算
    sale = await db_session.get(Sale, first.json()["id"])
    assert sale is not None
    sale.created_at = utc_now() - timedelta(minutes=91)
    await db_session.flush()
    assert await _recent(client, m, "A1") == []


async def test_recent_items_skip_voided_sales(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    m = await _seed(db_session)
    sold = await _sell(client, m, [_line(m.latte, [m.milk], refill=False)], "v1")
    assert sold.status_code == 201, sold.text
    sale = await db_session.get(Sale, sold.json()["id"])
    assert sale is not None
    sale.status = SaleStatus.VOIDED
    await db_session.flush()
    assert await _recent(client, m, "A1") == []
