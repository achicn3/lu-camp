"""發票佇列自動送出（背景）：作廢/折讓不能停在佇列裡等人按按鈕。

實機事實（2026-08-27）：F0401 開立由 `issue_for_sale` 同步送出、正常；但 F0501 作廢與
G0401 折讓**只進佇列、沒有任何東西會送**——後端啟動只跑備份與客顯兩個排程，前端也沒有
任何畫面呼叫 `/einvoice/queue/*`。結果是帳上作廢、平台上那張發票仍然有效（向 Amego
逐張查證：`invoice_status=99`、`cancel_date=0`）。

**自動送出刻意只涵蓋作廢與折讓**：開立牽涉字軌與客人當下要拿的發票，且佇列裡可能留有
大量歷史待開立列（本店實測 17,692 筆種子殘留），自動補開會把問題放大而不是修好。
開立的補救走人工（發票佇列頁的「重新開立」）。
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.einvoice.amego import AmegoClient, AmegoTransport
from app.modules.einvoice.background_service import (
    AUTO_SEND_MAX_AGE,
    AUTO_SEND_RETRY_INTERVAL,
    EInvoiceBackgroundService,
)
from app.modules.einvoice.models import EInvoiceUploadQueue, Invoice
from app.modules.einvoice.service import EInvoiceService
from app.modules.inventory.service import InventoryService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.service import SalesService
from app.modules.settings.models import StoreSettings
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    EInvoiceAction,
    Grade,
    InvoiceStatus,
    OwnershipType,
    SaleLineType,
    UploadStatus,
    UserRole,
)

_F0401_OK = {
    "code": 0,
    "msg": "",
    "invoice_number": "AB00002222",
    "invoice_time": 1783766130,
    "random_number": "1234",
    "barcode": "11507AB000022221234",
    "qrcode_left": "AB000022221150711...",
    "qrcode_right": "**相機...",
}
_QUERY_NOT_FOUND = {"code": 71, "msg": "查無資料"}  # 官方明載的查無碼
# 對帳查詢回應必須帶金額（真實 Amego 如此）：查到的紀錄要能證明是本筆。種子售價 1050。
_QUERY_INVOICE_OPEN: dict[str, object] = {
    "code": 0,
    "msg": "",
    "data": {"invoice_type": "C0401", "total_amount": 1050, "invoice_status": 99},
}
_F0501_OK = {"code": 0, "msg": ""}


class _RecordingTransport:
    """記錄每次呼叫的端點；回應依端點決定。

    開立與作廢**都會對帳先行**。首次查詢須回「查無」（平台還沒有這張，才會真的開立），
    之後查詢回「已開立」（作廢要能證明平台上確有這張才送得出 f0501）。
    """

    def __init__(self, *, f0501: object | None = None) -> None:
        self.calls: list[str] = []
        self._f0501 = f0501 if f0501 is not None else _F0501_OK
        self._queries = 0

    async def post_form(self, url: str, form: dict[str, str]) -> dict[str, object]:
        self.calls.append(url)
        if url.endswith("/json/invoice_query"):
            self._queries += 1
            return dict(_QUERY_NOT_FOUND) if self._queries == 1 else dict(_QUERY_INVOICE_OPEN)
        if url.endswith("/json/f0401"):
            return dict(_F0401_OK)
        if url.endswith("/json/f0501"):
            result = self._f0501
            if isinstance(result, Exception):
                raise result
            assert isinstance(result, dict)
            return dict(result)
        raise AssertionError(f"未預期的端點：{url}")

    @property
    def endpoints(self) -> list[str]:
        return [c.rsplit("/", 1)[-1] for c in self.calls]


def _client_factory(
    transport: AmegoTransport,
) -> Callable[[AsyncSession, int], Awaitable[AmegoClient]]:
    async def factory(_session: AsyncSession, _store_id: int) -> AmegoClient:
        return AmegoClient(
            seller_tax_id="12345678",
            app_key="test-key",
            transport=transport,
            base_url="https://invoice-api.amego.tw",
        )

    return factory


async def _seed_issued_and_voided_sale(
    session: AsyncSession, transport: _RecordingTransport
) -> int:
    """建一筆已開立、且已在系統內作廢的銷售 → 留下一筆 PENDING 的 F0501 佇列列。"""
    store = Store(name="自動送出門市", tax_id="12345678")
    session.add(store)
    await session.flush()
    manager = User(
        store_id=store.id, username="autosend-mgr", password_hash="h", role=UserRole.MANAGER
    )
    session.add(manager)
    await session.flush()
    session.add(StoreSettings(store_id=store.id, einvoice_enabled=True))
    await session.flush()
    await CashDrawerService(session).open_session(store.id, manager.id, Decimal("1000"))
    item = await InventoryService(session).create_serialized_item(
        store.id,
        item_code="SN-AUTO-1",
        name="相機",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal(1050),
        acquisition_cost=Decimal(500),
    )
    sales = SalesService(session)
    sale = await sales.create_sale(
        store.id,
        manager.id,
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=item.item_code)],
    )
    svc = EInvoiceService(session)
    issue_id = next(
        i.id for i in await svc.list_queue(store.id) if i.action is EInvoiceAction.ISSUE
    )
    factory = _client_factory(transport)
    await svc.send_via_amego(store.id, issue_id, client=await factory(session, store.id))
    await sales.void_sale(sale, manager.id)
    await session.commit()
    return store.id


async def _queue_row(
    session: AsyncSession, store_id: int, action: EInvoiceAction
) -> EInvoiceUploadQueue:
    row = await session.scalar(
        select(EInvoiceUploadQueue).where(
            EInvoiceUploadQueue.store_id == store_id,
            EInvoiceUploadQueue.action == action,
        )
    )
    assert row is not None
    return row


async def _age_queue_row(session: AsyncSession, queue_id: int, *, created_ago: timedelta) -> None:
    """把佇列列的時戳往回推（updated_at 也一併推，否則會被退避間隔擋住）。"""
    moment = datetime.now(UTC) - created_ago
    await session.execute(
        update(EInvoiceUploadQueue)
        .where(EInvoiceUploadQueue.id == queue_id)
        .values(created_at=moment, updated_at=moment)
    )
    await session.commit()


async def _truncate() -> None:
    factory = get_sessionmaker()
    async with factory() as session:
        await session.execute(text("TRUNCATE stores CASCADE"))
        await session.commit()


async def test_pending_void_is_sent_automatically() -> None:
    """核心：作廢排進佇列後，背景送出就該把它送到平台，不必等人按按鈕。"""
    factory = get_sessionmaker()
    try:
        transport = _RecordingTransport()
        async with factory() as session:
            store_id = await _seed_issued_and_voided_sale(session, transport)
            void_row = await _queue_row(session, store_id, EInvoiceAction.VOID)
            assert void_row.status is UploadStatus.PENDING  # 前提：目前確實停在佇列
            await _age_queue_row(session, void_row.id, created_ago=AUTO_SEND_RETRY_INTERVAL * 2)

        sent, failed = await EInvoiceBackgroundService.send_due_queue_items_once(
            client_factory=_client_factory(transport)
        )

        assert (sent, failed) == (1, 0)
        assert "f0501" in transport.endpoints
        async with factory() as session:
            row = await _queue_row(session, store_id, EInvoiceAction.VOID)
            assert row.status is UploadStatus.UPLOADED
            invoice = await session.scalar(select(Invoice).where(Invoice.store_id == store_id))
            assert invoice is not None and invoice.status is InvoiceStatus.VOID
    finally:
        await _truncate()


async def test_pending_issue_is_never_sent_automatically() -> None:
    """**絕不自動補開**：佇列裡可能留著大量歷史待開立列，自動送出等於整批補開。"""
    factory = get_sessionmaker()
    try:
        transport = _RecordingTransport()
        async with factory() as session:
            store = Store(name="待開立門市", tax_id="12345678")
            session.add(store)
            await session.flush()
            manager = User(
                store_id=store.id, username="pending-mgr", password_hash="h", role=UserRole.MANAGER
            )
            session.add(manager)
            await session.flush()
            session.add(StoreSettings(store_id=store.id, einvoice_enabled=True))
            await session.flush()
            await CashDrawerService(session).open_session(store.id, manager.id, Decimal("1000"))
            item = await InventoryService(session).create_serialized_item(
                store.id,
                item_code="SN-AUTO-2",
                name="相機",
                grade=Grade.A,
                ownership_type=OwnershipType.OWNED,
                listed_price=Decimal(1050),
                acquisition_cost=Decimal(500),
            )
            await SalesService(session).create_sale(
                store.id,
                manager.id,
                lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=item.item_code)],
            )
            await session.commit()
            store_id = store.id
            issue_row = await _queue_row(session, store_id, EInvoiceAction.ISSUE)
            await _age_queue_row(session, issue_row.id, created_ago=AUTO_SEND_RETRY_INTERVAL * 2)

        sent, failed = await EInvoiceBackgroundService.send_due_queue_items_once(
            client_factory=_client_factory(transport)
        )

        assert (sent, failed) == (0, 0)
        assert transport.calls == []  # 連對帳查詢都不該打
        async with factory() as session:
            row = await _queue_row(session, store_id, EInvoiceAction.ISSUE)
            assert row.status is UploadStatus.PENDING
    finally:
        await _truncate()


async def test_items_older_than_max_age_are_left_for_a_human() -> None:
    """陳年待送出不自動補送：越舊越可能是資料殘留或另有內情，交給人判斷。"""
    factory = get_sessionmaker()
    try:
        transport = _RecordingTransport()
        async with factory() as session:
            store_id = await _seed_issued_and_voided_sale(session, transport)
            void_row = await _queue_row(session, store_id, EInvoiceAction.VOID)
            await _age_queue_row(
                session, void_row.id, created_ago=AUTO_SEND_MAX_AGE + timedelta(days=1)
            )
        transport.calls.clear()

        sent, failed = await EInvoiceBackgroundService.send_due_queue_items_once(
            client_factory=_client_factory(transport)
        )

        assert (sent, failed) == (0, 0)
        assert transport.calls == []
        async with factory() as session:
            assert (
                await _queue_row(session, store_id, EInvoiceAction.VOID)
            ).status is UploadStatus.PENDING
    finally:
        await _truncate()


async def test_brand_new_item_is_sent_on_the_next_sweep() -> None:
    """店長按下作廢後，**下一輪就送**——不該為了退避而白等一個間隔。

    這段等待期間平台上那張發票仍然有效，等越久風險越大。退避只該套在重試上。
    """
    factory = get_sessionmaker()
    try:
        transport = _RecordingTransport()
        async with factory() as session:
            store_id = await _seed_issued_and_voided_sale(session, transport)
        # 刻意**不**把時戳往回推：模擬「剛剛才作廢」。

        sent, failed = await EInvoiceBackgroundService.send_due_queue_items_once(
            client_factory=_client_factory(transport)
        )

        assert (sent, failed) == (1, 0)
        async with factory() as session:
            assert (
                await _queue_row(session, store_id, EInvoiceAction.VOID)
            ).status is UploadStatus.UPLOADED
    finally:
        await _truncate()


async def test_already_attempted_item_waits_for_the_retry_interval() -> None:
    """已經試過一次的才套退避：連續重擊平台沒有意義，也可能被對方限流。"""
    factory = get_sessionmaker()
    try:
        # f0501 連線中斷 → 佇列列維持 PENDING 但已認領（xml_path 有值）
        transport = _RecordingTransport(f0501=RuntimeError("平台連線中斷"))
        async with factory() as session:
            store_id = await _seed_issued_and_voided_sale(session, transport)

        first = await EInvoiceBackgroundService.send_due_queue_items_once(
            client_factory=_client_factory(transport)
        )
        assert first == (0, 1)  # 第一次確實嘗試過了
        async with factory() as session:
            row = await _queue_row(session, store_id, EInvoiceAction.VOID)
            assert row.status is UploadStatus.PENDING
            assert row.xml_path is not None  # 已認領＝曾經嘗試
        calls_after_first = len(transport.calls)

        second = await EInvoiceBackgroundService.send_due_queue_items_once(
            client_factory=_client_factory(transport)
        )

        assert second == (0, 0)  # 退避中，這一輪不碰它
        assert len(transport.calls) == calls_after_first  # 完全沒有再打平台
    finally:
        await _truncate()


async def test_one_failing_item_does_not_stop_the_rest() -> None:
    """一筆送不出去不得讓整輪停擺——否則一筆壞資料會卡住之後所有的作廢。"""
    factory = get_sessionmaker()
    try:
        boom = _RecordingTransport(f0501=RuntimeError("平台連線中斷"))
        ok = _RecordingTransport()
        async with factory() as session:
            first = await _seed_issued_and_voided_sale(session, boom)
            row = await _queue_row(session, first, EInvoiceAction.VOID)
            await _age_queue_row(session, row.id, created_ago=AUTO_SEND_RETRY_INTERVAL * 2)

        sent, failed = await EInvoiceBackgroundService.send_due_queue_items_once(
            client_factory=_client_factory(boom)
        )

        assert (sent, failed) == (0, 1)  # 明確計為失敗，不是靜默略過
        async with factory() as session:
            assert (
                await _queue_row(session, first, EInvoiceAction.VOID)
            ).status is UploadStatus.PENDING
        assert ok.calls == []
    finally:
        await _truncate()
