"""餐飲內用／外帶報表的口徑（docs/39）。

三條紅線（寫錯會誤導經營判斷，不只是數字不好看）：
1. **佔比的分母是「有餐飲的單」**——用全店訂單當分母，多賣幾台二手裝備就會把
   內用佔比稀釋掉，看起來像內用變差，實際餐飲完全沒變。
2. **客單價只算 MENU 行**——同一張單的二手成交會把餐飲客單價灌水。
3. **時段以台北時間切**——用 UTC 會讓尖峰整體位移 8 小時。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.core.time import STORE_TIME_ZONE
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.models import CatalogProduct
from app.modules.menu.models import MenuItem
from app.modules.reports.service import ReportsService
from app.modules.sales.inputs import SaleLineInput, TenderInput
from app.modules.sales.models import GiftReason, Sale
from app.modules.sales.service import SalesService
from app.modules.settings.models import StoreSettings
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    SaleLineKind,
    SaleLineType,
    SaleStatus,
    ServiceMode,
    TenderType,
    UserRole,
)
from app.shared.exceptions import SaleLineInvalid

pytestmark = pytest.mark.asyncio


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


class Ctx:
    def __init__(
        self, store_id: int, user_id: int, menu_id: int, product_id: int, gift_reason_id: int
    ) -> None:
        self.store_id = store_id
        self.user_id = user_id
        self.menu_id = menu_id
        self.product_id = product_id
        self.gift_reason_id = gift_reason_id


async def _seed(session: AsyncSession) -> Ctx:
    store = Store(name="餐飲報表店", tax_id="12345678")
    session.add(store)
    await session.flush()
    user = User(
        store_id=store.id, username=f"di-{store.id}", password_hash="h", role=UserRole.MANAGER
    )
    session.add(user)
    session.add(StoreSettings(store_id=store.id, dine_in_tables=["A1", "A2"]))
    await session.flush()
    await CashDrawerService(session).open_session(store.id, user.id, Decimal("5000"))
    menu = MenuItem(store_id=store.id, name="手沖-耶加", unit_price=Decimal("180"))
    product = CatalogProduct(
        store_id=store.id,
        sku=f"DI-{store.id}",
        name="露營燈",
        unit_price=Decimal("500"),
        unit_cost=Decimal("200"),
        quantity_on_hand=100,
    )
    gift_reason = GiftReason(store_id=store.id, code="PROMO", name="活動贈品")
    session.add_all([menu, product, gift_reason])
    await session.flush()
    return Ctx(store.id, user.id, menu.id, product.id, gift_reason.id)


def _window() -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    return now - timedelta(days=1), now + timedelta(days=1)


async def _menu_sale(
    session: AsyncSession,
    ctx: Ctx,
    *,
    mode: ServiceMode,
    qty: int = 1,
    with_product: bool = False,
) -> Sale:
    lines = [SaleLineInput(line_type=SaleLineType.MENU, menu_item_id=ctx.menu_id, qty=qty)]
    total = Decimal(180) * qty
    if with_product:
        lines.append(
            SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=ctx.product_id, qty=1)
        )
        total += Decimal(500)
    return await SalesService(session).create_sale(
        ctx.store_id,
        ctx.user_id,
        lines=lines,
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=total)],
        service_mode=mode,
        table_no="A1" if mode is ServiceMode.DINE_IN else None,
    )


async def _product_only_sale(session: AsyncSession, ctx: Ctx) -> Sale:
    return await SalesService(session).create_sale(
        ctx.store_id,
        ctx.user_id,
        lines=[
            SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=ctx.product_id, qty=1)
        ],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(500))],
    )


# ── 組數與佔比 ──────────────────────────────────────────────


async def test_counts_one_group_per_checkout(db_session: AsyncSession) -> None:
    """裁示：一筆結帳算一組（不論點了幾杯）。"""
    ctx = await _seed(db_session)
    await _menu_sale(db_session, ctx, mode=ServiceMode.DINE_IN, qty=3)
    await _menu_sale(db_session, ctx, mode=ServiceMode.TAKEOUT)
    date_from, date_to = _window()
    report = await ReportsService(db_session).dine_in_report(
        ctx.store_id, date_from=date_from, date_to=date_to, granularity="day"
    )
    assert (report.summary.dine_in.groups, report.summary.takeout.groups) == (1, 1)


async def test_share_denominator_is_fnb_sales_not_all_sales(db_session: AsyncSession) -> None:
    """**最容易寫錯的一條**：分母是「有餐飲的單」。

    加入大量純二手單之後，內用佔比**不得改變**——否則某天多賣幾台裝備，
    店主會以為內用生意變差。
    """
    ctx = await _seed(db_session)
    await _menu_sale(db_session, ctx, mode=ServiceMode.DINE_IN)
    await _menu_sale(db_session, ctx, mode=ServiceMode.TAKEOUT)
    date_from, date_to = _window()
    svc = ReportsService(db_session)
    before = (
        await svc.dine_in_report(
            ctx.store_id, date_from=date_from, date_to=date_to, granularity="day"
        )
    ).summary.dine_in.share

    for _ in range(8):
        await _product_only_sale(db_session, ctx)

    after = (
        await svc.dine_in_report(
            ctx.store_id, date_from=date_from, date_to=date_to, granularity="day"
        )
    ).summary.dine_in.share
    assert before == after == Decimal("0.5")


async def test_product_only_sales_are_not_counted_at_all(db_session: AsyncSession) -> None:
    ctx = await _seed(db_session)
    await _product_only_sale(db_session, ctx)
    date_from, date_to = _window()
    report = await ReportsService(db_session).dine_in_report(
        ctx.store_id, date_from=date_from, date_to=date_to, granularity="day"
    )
    assert (report.summary.dine_in.groups, report.summary.takeout.groups) == (0, 0)


async def test_voided_sales_are_excluded(db_session: AsyncSession) -> None:
    ctx = await _seed(db_session)
    sale = await _menu_sale(db_session, ctx, mode=ServiceMode.DINE_IN)
    sale.status = SaleStatus.VOIDED
    await db_session.flush()
    date_from, date_to = _window()
    report = await ReportsService(db_session).dine_in_report(
        ctx.store_id, date_from=date_from, date_to=date_to, granularity="day"
    )
    assert report.summary.dine_in.groups == 0


# ── 客單價 ──────────────────────────────────────────────────


async def test_average_ticket_counts_only_menu_lines(db_session: AsyncSession) -> None:
    """一張單同時有餐飲與二手：組數算一組，**餐飲客單價只算 MENU 行**。

    把整單金額當客單價，內用客單價會被同行的二手成交灌水（180 → 680）。
    """
    ctx = await _seed(db_session)
    await _menu_sale(db_session, ctx, mode=ServiceMode.DINE_IN, with_product=True)
    date_from, date_to = _window()
    report = await ReportsService(db_session).dine_in_report(
        ctx.store_id, date_from=date_from, date_to=date_to, granularity="day"
    )
    dine_in = report.summary.dine_in
    assert dine_in.groups == 1
    assert dine_in.fnb_revenue == Decimal(180)
    assert dine_in.avg_ticket == Decimal(180)
    # 整單合計另列供對照，不與客單價混用
    assert dine_in.gross_total == Decimal(680)


# ── 趨勢與時段 ──────────────────────────────────────────────


async def test_trend_buckets_sum_to_the_period_total(db_session: AsyncSession) -> None:
    """各桶加總必須等於全期——分桶算錯時這條會先紅。"""
    ctx = await _seed(db_session)
    await _menu_sale(db_session, ctx, mode=ServiceMode.DINE_IN)
    await _menu_sale(db_session, ctx, mode=ServiceMode.TAKEOUT)
    date_from, date_to = _window()
    report = await ReportsService(db_session).dine_in_report(
        ctx.store_id, date_from=date_from, date_to=date_to, granularity="day"
    )
    assert sum(b.dine_in_groups for b in report.trend) == report.summary.dine_in.groups
    assert sum(b.takeout_groups for b in report.trend) == report.summary.takeout.groups


async def test_hourly_buckets_use_taipei_time(db_session: AsyncSession) -> None:
    """**時段必須以台北時間切**：用 UTC 會讓尖峰整體位移 8 小時。"""
    ctx = await _seed(db_session)
    sale = await _menu_sale(db_session, ctx, mode=ServiceMode.DINE_IN)
    # 台北 14:30（UTC 06:30）
    taipei_1430 = datetime(2026, 8, 19, 14, 30, tzinfo=STORE_TIME_ZONE)
    sale.created_at = taipei_1430.astimezone(UTC)
    await db_session.flush()
    report = await ReportsService(db_session).dine_in_report(
        ctx.store_id,
        date_from=datetime(2026, 8, 19, tzinfo=STORE_TIME_ZONE),
        date_to=datetime(2026, 8, 20, tzinfo=STORE_TIME_ZONE),
        granularity="day",
    )
    at_14 = next(h for h in report.hourly if h.hour == 14)
    assert at_14.dine_in_groups == 1
    assert all(h.dine_in_groups == 0 for h in report.hourly if h.hour != 14)


async def test_hourly_has_all_24_buckets(db_session: AsyncSession) -> None:
    """空桶補 0——折線/長條圖不該因為某小時沒生意就少一格。"""
    ctx = await _seed(db_session)
    date_from, date_to = _window()
    report = await ReportsService(db_session).dine_in_report(
        ctx.store_id, date_from=date_from, date_to=date_to, granularity="day"
    )
    assert [h.hour for h in report.hourly] == list(range(24))


async def test_other_stores_data_is_not_counted(db_session: AsyncSession) -> None:
    ctx_a = await _seed(db_session)
    ctx_b = await _seed(db_session)
    await _menu_sale(db_session, ctx_b, mode=ServiceMode.DINE_IN)
    date_from, date_to = _window()
    report = await ReportsService(db_session).dine_in_report(
        ctx_a.store_id, date_from=date_from, date_to=date_to, granularity="day"
    )
    assert report.summary.dine_in.groups == 0


# ── API ─────────────────────────────────────────────────────


async def test_endpoint_requires_manager(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """報表一律限店長（與既有報表一致）。"""
    ctx = await _seed(db_session)
    clerk = User(
        store_id=ctx.store_id, username=f"di-clk-{ctx.store_id}", password_hash="h",
        role=UserRole.CLERK,
    )
    db_session.add(clerk)
    await db_session.flush()
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=ctx.store_id)
    now = datetime.now(UTC)
    resp = await client.get(
        "/api/v1/reports/dine-in",
        params={"from": (now - timedelta(days=1)).isoformat(), "to": now.isoformat()},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403


async def test_endpoint_rejects_bad_range_and_granularity(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _seed(db_session)
    token = encode_access_token(user_id=ctx.user_id, role="MANAGER", store_id=ctx.store_id)
    auth = {"Authorization": f"Bearer {token}"}
    now = datetime.now(UTC)
    same = {"from": now.isoformat(), "to": now.isoformat()}
    resp_same = await client.get("/api/v1/reports/dine-in", params=same, headers=auth)
    assert resp_same.status_code == 422
    bad = {
        "from": (now - timedelta(days=1)).isoformat(),
        "to": now.isoformat(),
        "granularity": "fortnight",
    }
    resp_bad = await client.get("/api/v1/reports/dine-in", params=bad, headers=auth)
    assert resp_bad.status_code == 422


async def test_endpoint_returns_the_report(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _seed(db_session)
    await _menu_sale(db_session, ctx, mode=ServiceMode.DINE_IN)
    await _menu_sale(db_session, ctx, mode=ServiceMode.TAKEOUT)
    await _menu_sale(db_session, ctx, mode=ServiceMode.TAKEOUT)
    token = encode_access_token(user_id=ctx.user_id, role="MANAGER", store_id=ctx.store_id)
    now = datetime.now(UTC)
    resp = await client.get(
        "/api/v1/reports/dine-in",
        params={
            "from": (now - timedelta(days=1)).isoformat(),
            "to": (now + timedelta(days=1)).isoformat(),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"]["dine_in"]["groups"] == 1
    assert body["summary"]["takeout"]["groups"] == 2
    assert len(body["hourly"]) == 24


# ── 自審補的邊界 ────────────────────────────────────────────


async def test_menu_item_cannot_be_a_gift(db_session: AsyncSession) -> None:
    """**餐飲不可作為贈品**（既有規則），所以不會出現「營收 0 的餐飲組」。

    這條在此重述是為了釘住報表的一個前提：本報表以「餐飲營收 > 0」判斷是不是一組，
    只有在餐飲行永遠有金額時才成立。哪天放寬贈品規則，這條會先紅，
    提醒去改報表的判定（否則送一杯的那組客人會整組不見）。
    """
    ctx = await _seed(db_session)
    with pytest.raises(SaleLineInvalid):
        await SalesService(db_session).create_sale(
            ctx.store_id,
            ctx.user_id,
            lines=[
                SaleLineInput(
                    line_type=SaleLineType.MENU,
                    menu_item_id=ctx.menu_id,
                    qty=1,
                    line_kind=SaleLineKind.GIFT,
                    gift_reason_id=ctx.gift_reason_id,
                )
            ],
            tenders=None,
            service_mode=ServiceMode.DINE_IN,
            table_no="A1",
        )


async def test_unknown_service_mode_is_skipped_not_miscounted(
    db_session: AsyncSession
) -> None:
    """未知服務型態**寧可不算，也不要算錯**（防禦分支先前零覆蓋）。"""
    ctx = await _seed(db_session)
    await _menu_sale(db_session, ctx, mode=ServiceMode.DINE_IN)
    svc = ReportsService(db_session)
    original = svc._sales.dine_in_rows

    async def _with_bogus_mode(
        store_id: int, date_from: datetime, date_to: datetime
    ) -> list[tuple[datetime, str, Decimal, Decimal]]:
        rows = await original(store_id, date_from, date_to)
        return [*rows, (datetime.now(UTC), "SOMETHING_NEW", Decimal(999), Decimal(999))]

    svc._sales.dine_in_rows = _with_bogus_mode  # type: ignore[method-assign]
    date_from, date_to = _window()
    report = await svc.dine_in_report(
        ctx.store_id, date_from=date_from, date_to=date_to, granularity="day"
    )
    # 未知型態既不算內用也不算外帶，更不得混進營收
    assert report.summary.dine_in.groups == 1
    assert report.summary.takeout.groups == 0
    assert report.summary.dine_in.fnb_revenue == Decimal(180)


async def test_export_csv_carries_the_basis_notes(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """匯出的檔案**必須帶上口徑**：檔案離開系統後，畫面上那兩句提醒就跟不過去了。

    只有數字的 CSV 被轉寄出去，收到的人一定會拿內用/外帶的客單價直接比較。
    """
    ctx = await _seed(db_session)
    await _menu_sale(db_session, ctx, mode=ServiceMode.DINE_IN)
    token = encode_access_token(user_id=ctx.user_id, role="MANAGER", store_id=ctx.store_id)
    now = datetime.now(UTC)
    resp = await client.get(
        "/api/v1/reports/dine-in",
        params={
            "from": (now - timedelta(days=1)).isoformat(),
            "to": (now + timedelta(days=1)).isoformat(),
            "format": "csv",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.content.decode("utf-8-sig")
    assert "有餐飲的單" in body
    assert "不可直接比較" in body
    assert "內用" in body and "外帶" in body
    # 畫面上有三個區塊，匯出不得只帶兩個——時段分佈是店主指名的四個指標之一
    assert "期間起" in body, "缺趨勢區塊"
    assert "時段（台北）" in body, "缺時段分佈區塊"
    assert "00:00" in body and "23:00" in body, "時段應涵蓋 24 小時"


async def test_trend_period_is_the_taipei_business_day(db_session: AsyncSession) -> None:
    """**趨勢的日期標籤必須是台北營業日**。

    分桶起點在內部是 UTC；台北 22:00 的交易，其日桶起點是 UTC 前一天 16:00。
    若把那個 UTC 時間直接當日期顯示，畫面與匯出都會**悄悄差一天**——
    數字全對、總和也對，只有標籤錯位，店主拿去對「哪天生意好」會全盤錯。
    """
    ctx = await _seed(db_session)
    sale = await _menu_sale(db_session, ctx, mode=ServiceMode.DINE_IN)
    taipei_2200 = datetime(2026, 8, 19, 22, 0, tzinfo=STORE_TIME_ZONE)
    sale.created_at = taipei_2200.astimezone(UTC)
    await db_session.flush()

    report = await ReportsService(db_session).dine_in_report(
        ctx.store_id,
        date_from=datetime(2026, 8, 19, tzinfo=STORE_TIME_ZONE),
        date_to=datetime(2026, 8, 21, tzinfo=STORE_TIME_ZONE),
        granularity="day",
    )
    # 空桶補 0，所以 8/20 也會在；重點是有交易的那天標成 **8/19** 而非 UTC 的 8/18
    assert [b.period.isoformat() for b in report.trend] == ["2026-08-19", "2026-08-20"]
    assert [b.dine_in_groups for b in report.trend] == [1, 0]


async def test_trend_fills_empty_buckets_with_zero(db_session: AsyncSession) -> None:
    """**沒生意的那天也要出現在趨勢上**（docs/39 §4「空桶補 0」）。

    只輸出有交易的桶，看報表的人分不出「那天沒生意」與「那天不在查詢範圍內」，
    折線圖也會直接跳過去。實測畫面上就少了 8/18 那一列。
    """
    ctx = await _seed(db_session)
    sale = await _menu_sale(db_session, ctx, mode=ServiceMode.DINE_IN)
    sale.created_at = datetime(2026, 8, 17, 12, 0, tzinfo=STORE_TIME_ZONE).astimezone(UTC)
    await db_session.flush()

    report = await ReportsService(db_session).dine_in_report(
        ctx.store_id,
        date_from=datetime(2026, 8, 17, tzinfo=STORE_TIME_ZONE),
        date_to=datetime(2026, 8, 20, tzinfo=STORE_TIME_ZONE),
        granularity="day",
    )
    assert [b.period.isoformat() for b in report.trend] == [
        "2026-08-17",
        "2026-08-18",
        "2026-08-19",
    ]
    assert [b.dine_in_groups for b in report.trend] == [1, 0, 0]


async def test_trend_rejects_too_many_buckets(db_session: AsyncSession) -> None:
    """日粒度跨數年會產生上千桶——擋下來，不要把瀏覽器與匯出撐爆。"""
    ctx = await _seed(db_session)
    with pytest.raises(ValueError):
        await ReportsService(db_session).dine_in_report(
            ctx.store_id,
            date_from=datetime(2020, 1, 1, tzinfo=STORE_TIME_ZONE),
            date_to=datetime(2026, 1, 1, tzinfo=STORE_TIME_ZONE),
            granularity="day",
        )


async def test_endpoint_maps_too_many_buckets_to_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """桶數超限是**使用者的查詢條件問題**，要回 422 而非 500。"""
    ctx = await _seed(db_session)
    token = encode_access_token(user_id=ctx.user_id, role="MANAGER", store_id=ctx.store_id)
    resp = await client.get(
        "/api/v1/reports/dine-in",
        params={
            "from": datetime(2020, 1, 1, tzinfo=UTC).isoformat(),
            "to": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "granularity": "day",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text
    assert "分桶" in resp.json()["detail"]
