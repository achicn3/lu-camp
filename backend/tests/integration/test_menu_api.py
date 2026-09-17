"""menu API 整合測試：餐飲菜單品項 CRUD、RBAC、去重、改價稽核、封存、store 隔離。"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.menu.models import MenuItem
from app.modules.menu.service import MenuService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole
from app.shared.exceptions import SaleLineInvalid


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


async def _seed(session: AsyncSession) -> tuple[str, str, int]:
    """建店+店員+經理，回 (clerk_token, manager_token, store_id)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    session.add_all([clerk, mgr])
    await session.flush()
    return (
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
        store.id,
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_create_list_menu_item(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, mgr, _ = await _seed(db_session)
    resp = await client.post(
        "/api/v1/menu-items",
        json={"name": "手沖-耶加", "unit_price": "180", "category": "咖啡"},
        headers=_auth(mgr),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "手沖-耶加"
    assert body["unit_price"] == "180"  # 字串傳輸
    assert body["is_available"] is True

    listed = await client.get("/api/v1/menu-items", headers=_auth(mgr))
    assert listed.status_code == 200
    assert [i["name"] for i in listed.json()] == ["手沖-耶加"]


async def test_create_with_cost_and_update_it(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """成本可填、可改、可清空（裁示 2026-09-17）。

    餐飲原本完全沒有成本概念，賣出時成本記成未知，報表只看得到營收、算不出毛利。
    成本由店主自行加總（豆子、耗材、包材…）後填一個數字——要讓系統自動算得建原料
    主檔與配方用量，單店不划算。
    """
    _, mgr, _ = await _seed(db_session)
    created = await client.post(
        "/api/v1/menu-items",
        json={"name": "拿鐵", "unit_price": "150", "unit_cost": "45"},
        headers=_auth(mgr),
    )
    assert created.status_code == 201, created.text
    assert created.json()["unit_cost"] == "45"  # 字串傳輸（§11）
    item_id = created.json()["id"]

    changed = await client.patch(
        f"/api/v1/menu-items/{item_id}", json={"unit_cost": "52"}, headers=_auth(mgr)
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["unit_cost"] == "52"
    assert changed.json()["unit_price"] == "150"  # 未提供的欄位不動

    cleared = await client.patch(
        f"/api/v1/menu-items/{item_id}", json={"unit_cost": None}, headers=_auth(mgr)
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["unit_cost"] is None  # 不知道成本就誠實留空，不要填 0


async def test_cost_is_optional_and_must_be_a_whole_non_negative_amount(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr, _ = await _seed(db_session)
    no_cost = await client.post(
        "/api/v1/menu-items", json={"name": "白開水", "unit_price": "10"}, headers=_auth(mgr)
    )
    assert no_cost.status_code == 201
    assert no_cost.json()["unit_cost"] is None

    for bad in ("-1", "12.5"):
        resp = await client.post(
            "/api/v1/menu-items",
            json={"name": f"壞成本{bad}", "unit_price": "100", "unit_cost": bad},
            headers=_auth(mgr),
        )
        assert resp.status_code == 422, f"{bad} 應被擋下：{resp.text}"


async def test_duplicate_name_409(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, mgr, _ = await _seed(db_session)
    payload = {"name": "拿鐵", "unit_price": "150"}
    first = await client.post("/api/v1/menu-items", json=payload, headers=_auth(mgr))
    assert first.status_code == 201
    dup = await client.post("/api/v1/menu-items", json=payload, headers=_auth(mgr))
    assert dup.status_code == 409


async def test_invalid_price_422(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, mgr, _ = await _seed(db_session)
    # 0 元（gt=0 schema 擋）→ 422
    resp = await client.post(
        "/api/v1/menu-items", json={"name": "贈品", "unit_price": "0"}, headers=_auth(mgr)
    )
    assert resp.status_code == 422


async def test_clerk_cannot_write_but_can_list(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, _ = await _seed(db_session)
    await client.post(
        "/api/v1/menu-items", json={"name": "美式", "unit_price": "120"}, headers=_auth(mgr)
    )
    # 店員可讀（POS 取菜單）
    listed = await client.get("/api/v1/menu-items", headers=_auth(clerk))
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    # 店員不可建
    denied = await client.post(
        "/api/v1/menu-items", json={"name": "卡布", "unit_price": "140"}, headers=_auth(clerk)
    )
    assert denied.status_code == 403


async def test_update_price_writes_audit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr, _ = await _seed(db_session)
    created = await client.post(
        "/api/v1/menu-items", json={"name": "卡布奇諾", "unit_price": "150"}, headers=_auth(mgr)
    )
    item_id = created.json()["id"]
    upd = await client.patch(
        f"/api/v1/menu-items/{item_id}", json={"unit_price": "160"}, headers=_auth(mgr)
    )
    assert upd.status_code == 200
    assert upd.json()["unit_price"] == "160"
    audits = (
        await db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "UPDATE_MENU_ITEM_PRICE")
        )
    )
    assert audits == 1


async def test_available_only_filter(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr, _ = await _seed(db_session)
    created = await client.post(
        "/api/v1/menu-items", json={"name": "季節限定", "unit_price": "200"}, headers=_auth(mgr)
    )
    item_id = created.json()["id"]
    # 下架
    await client.patch(
        f"/api/v1/menu-items/{item_id}", json={"is_available": False}, headers=_auth(mgr)
    )
    # 管理列表含停售
    full = await client.get("/api/v1/menu-items", headers=_auth(mgr))
    assert len(full.json()) == 1
    # POS 只列可售 → 空
    pos = await client.get("/api/v1/menu-items?available_only=true", headers=_auth(mgr))
    assert pos.json() == []


async def test_archive_hides_from_list(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr, _ = await _seed(db_session)
    created = await client.post(
        "/api/v1/menu-items", json={"name": "已停售品", "unit_price": "100"}, headers=_auth(mgr)
    )
    item_id = created.json()["id"]
    deleted = await client.delete(f"/api/v1/menu-items/{item_id}", headers=_auth(mgr))
    assert deleted.status_code == 200
    listed = await client.get("/api/v1/menu-items", headers=_auth(mgr))
    assert listed.json() == []
    # 二次刪除 → 404（已封存）
    again = await client.delete(f"/api/v1/menu-items/{item_id}", headers=_auth(mgr))
    assert again.status_code == 404


async def test_store_isolation(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, mgr_a, _ = await _seed(db_session)
    # 另一間店（真實建檔）的品項不應出現在 A 店清單，也不可被 A 店改價（404）。
    store_b = Store(name="他店")
    db_session.add(store_b)
    await db_session.flush()
    other = MenuItem(store_id=store_b.id, name="他店品", unit_price=Decimal("99"))
    db_session.add(other)
    await db_session.flush()
    listed = await client.get("/api/v1/menu-items", headers=_auth(mgr_a))
    assert all(i["name"] != "他店品" for i in listed.json())
    resp = await client.patch(
        f"/api/v1/menu-items/{other.id}", json={"unit_price": "1"}, headers=_auth(mgr_a)
    )
    assert resp.status_code == 404


async def test_cost_invariants_enforced_in_service(db_session: AsyncSession) -> None:
    """成本不變量歸 service（CLAUDE.md §2）：負值／小數／超額一律擋下。

    負成本會讓報表高估毛利，所以不能只靠 schema——service 是唯一保證不變量的地方，
    其他呼叫端（腳本、跨模組）不會經過 HTTP。
    """
    _, _, store_id = await _seed(db_session)
    actor = await db_session.scalar(select(User.id).where(User.username == "mgr"))
    assert actor is not None
    svc = MenuService(db_session)
    for bad in (Decimal("-1"), Decimal("1.5"), Decimal("1000000000000")):
        with pytest.raises(SaleLineInvalid):
            await svc.create_menu_item(
                store_id,
                name=f"壞成本-{bad}",
                unit_price=Decimal("100"),
                unit_cost=bad,
                actor_user_id=actor,
            )
    good = await svc.create_menu_item(
        store_id,
        name="正常品",
        unit_price=Decimal("100"),
        unit_cost=Decimal("0"),
        actor_user_id=actor,
    )
    assert good.unit_cost == Decimal("0")  # 0＝已知零成本，與 None（未知）不同
    for bad in (Decimal("-1"), Decimal("1.5")):
        with pytest.raises(SaleLineInvalid):
            await svc.update_menu_item(store_id, good.id, unit_cost=bad, actor_user_id=actor)


async def test_negative_cost_rejected_by_api(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr, _ = await _seed(db_session)
    resp = await client.post(
        "/api/v1/menu-items",
        json={"name": "負成本", "unit_price": "100", "unit_cost": "-1"},
        headers=_auth(mgr),
    )
    assert resp.status_code == 422, resp.text


async def test_update_without_cost_keeps_it(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """沒帶 unit_cost 的更新＝不動成本；帶 null 才是清空（_UNSET 哨兵語意）。"""
    _, mgr, _ = await _seed(db_session)
    created = await client.post(
        "/api/v1/menu-items",
        json={"name": "拿鐵", "unit_price": "150", "unit_cost": "45"},
        headers=_auth(mgr),
    )
    item_id = created.json()["id"]
    renamed = await client.patch(
        f"/api/v1/menu-items/{item_id}", json={"name": "拿鐵（大）"}, headers=_auth(mgr)
    )
    assert renamed.status_code == 200
    assert renamed.json()["unit_cost"] == "45"  # 只改名不該把成本弄丟
    cleared = await client.patch(
        f"/api/v1/menu-items/{item_id}", json={"unit_cost": None}, headers=_auth(mgr)
    )
    assert cleared.status_code == 200
    assert cleared.json()["unit_cost"] is None


async def test_zero_cost_is_known_not_unknown(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """明確填 0＝成本已知為零（例如贈飲用料另計），不可被當成「未知」。"""
    _, mgr, _ = await _seed(db_session)
    created = await client.post(
        "/api/v1/menu-items",
        json={"name": "白開水", "unit_price": "10", "unit_cost": "0"},
        headers=_auth(mgr),
    )
    assert created.status_code == 201, created.text
    assert created.json()["unit_cost"] == "0"


async def test_cost_change_is_audited(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """成本會直接影響毛利報表，改動要留前後值（§5 敏感操作）。"""
    _, mgr, _ = await _seed(db_session)
    created = await client.post(
        "/api/v1/menu-items",
        json={"name": "拿鐵", "unit_price": "150", "unit_cost": "45"},
        headers=_auth(mgr),
    )
    item_id = created.json()["id"]
    await client.patch(
        f"/api/v1/menu-items/{item_id}", json={"unit_cost": "60"}, headers=_auth(mgr)
    )
    await client.patch(
        f"/api/v1/menu-items/{item_id}", json={"unit_cost": None}, headers=_auth(mgr)
    )
    logs = (
        await db_session.scalars(
            select(AuditLog)
            .where(AuditLog.action == "UPDATE_MENU_ITEM_COST")
            .order_by(AuditLog.id)
        )
    ).all()
    assert [(log.before["unit_cost"], log.after["unit_cost"]) for log in logs] == [
        ("45", "60"),
        ("60", None),
    ]
