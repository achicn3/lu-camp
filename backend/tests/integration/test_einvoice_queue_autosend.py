"""發票佇列自動送出（背景）：作廢/折讓不能停在佇列裡等人按按鈕。

實機事實（2026-08-27）：F0401 開立由 `issue_for_sale` 同步送出、正常；但 F0501 作廢與
G0401 折讓**只進佇列、沒有任何東西會送**——後端啟動只跑備份與客顯兩個排程，前端也沒有
任何畫面呼叫 `/einvoice/queue/*`。結果是帳上作廢、平台上那張發票仍然有效（向 Amego
逐張查證：`invoice_status=99`、`cancel_date=0`）。

**自動送出刻意只涵蓋作廢與折讓**：開立牽涉字軌與客人當下要拿的發票，且佇列裡可能留有
大量歷史待開立列（本店實測 17,692 筆種子殘留），自動補開會把問題放大而不是修好。
開立的補救走人工（發票佇列頁的「重新開立」）。
"""

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.einvoice.amego import AmegoClient, AmegoTransport
from app.modules.einvoice.background_service import (
    AUTO_SEND_MESSAGE_TYPES,
    AUTO_SEND_RETRY_INTERVAL,
    EInvoiceBackgroundService,
)
from app.modules.einvoice.models import EInvoiceUploadQueue, Invoice, InvoiceAllowance
from app.modules.einvoice.service import EInvoiceService
from app.modules.inventory.service import InventoryService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.service import SalesService
from app.modules.settings.models import StoreSettings
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    EInvoiceAction,
    EInvoiceMessageType,
    Grade,
    InvoiceStatus,
    OwnershipType,
    SaleLineType,
    UploadStatus,
    UserRole,
)
from app.shared.exceptions import EInvoiceQueueNotDroppable

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
        self.forms: list[tuple[str, dict[str, str]]] = []
        self._f0501 = f0501 if f0501 is not None else _F0501_OK
        self._queries = 0

    async def post_form(self, url: str, form: dict[str, str]) -> dict[str, object]:
        self.calls.append(url)
        self.forms.append((url, form))
        if url.endswith("/json/allowance_query"):
            return {"code": 71, "msg": "查無資料"}
        if url.endswith("/json/g0401"):
            return {"code": 0, "msg": ""}
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


class _FlakyVoidTransport(_RecordingTransport):
    """第一次 f0501 中斷、之後正常——用來驗「一筆失敗不影響其餘」。"""

    def __init__(self, *, fail_first: bool = True) -> None:
        super().__init__()
        self._fail_next_void = fail_first

    async def post_form(self, url: str, form: dict[str, str]) -> dict[str, object]:
        if url.endswith("/json/f0501") and self._fail_next_void:
            self._fail_next_void = False
            self.calls.append(url)
            raise RuntimeError("平台連線中斷")
        return await super().post_form(url, form)


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
    session: AsyncSession,
    transport: _RecordingTransport,
    *,
    name: str = "自動送出門市",
    code: str = "SN-AUTO-1",
) -> int:
    """建一筆已開立、且已在系統內作廢的銷售 → 留下一筆 PENDING 的 F0501 佇列列。"""
    store = Store(name=name, tax_id="12345678")
    session.add(store)
    await session.flush()
    manager = User(
        store_id=store.id,
        username=f"autosend-mgr-{store.id}",
        password_hash="h",
        role=UserRole.MANAGER,
    )
    session.add(manager)
    await session.flush()
    session.add(StoreSettings(store_id=store.id, einvoice_enabled=True))
    await session.flush()
    await CashDrawerService(session).open_session(store.id, manager.id, Decimal("1000"))
    item = await InventoryService(session).create_serialized_item(
        store.id,
        item_code=code,
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


async def test_very_old_pending_void_is_still_sent() -> None:
    """陳年的待送出**照樣要送**——放越久代表平台上那張發票已經有效越久。

    曾經有過 7 天的年齡上限（為了防歷史待開立列被整批補送），但那件事由訊息型別守衛
    擋住即可；上限反而讓舊的作廢永遠沒人送、紅點也照不到（Codex 第三輪 P1）。
    """
    factory = get_sessionmaker()
    try:
        transport = _RecordingTransport()
        async with factory() as session:
            store_id = await _seed_issued_and_voided_sale(session, transport)
            void_row = await _queue_row(session, store_id, EInvoiceAction.VOID)
            await _age_queue_row(session, void_row.id, created_ago=timedelta(days=45))
        transport.calls.clear()

        sent, failed = await EInvoiceBackgroundService.send_due_queue_items_once(
            client_factory=_client_factory(transport)
        )

        assert (sent, failed) == (1, 0)
        assert "f0501" in transport.endpoints
        async with factory() as session:
            assert (
                await _queue_row(session, store_id, EInvoiceAction.VOID)
            ).status is UploadStatus.UPLOADED
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
    """一筆送不出去不得讓整輪停擺——否則一筆壞資料會卡住之後所有的作廢。

    **必須有兩筆**（第一筆會炸、第二筆會成功）才守得住這件事：只放一筆的話，
    就算實作在例外後直接 break，測試照樣通過（Codex 第三輪指出的空斷言）。
    """
    factory = get_sessionmaker()
    try:
        # 第一筆的 f0501 會炸；第二筆正常。以送出順序（created_at 由舊到新）決定誰先。
        first_transport = _FlakyVoidTransport(fail_first=True)
        async with factory() as session:
            store_a = await _seed_issued_and_voided_sale(
                session, first_transport, name="失敗門市", code="SN-FAIL"
            )
            row_a = await _queue_row(session, store_a, EInvoiceAction.VOID)
            await _age_queue_row(session, row_a.id, created_ago=timedelta(hours=2))
        async with factory() as session:
            store_b = await _seed_issued_and_voided_sale(
                session, _RecordingTransport(), name="成功門市", code="SN-OK"
            )
            row_b = await _queue_row(session, store_b, EInvoiceAction.VOID)
            await _age_queue_row(session, row_b.id, created_ago=timedelta(hours=1))

        sent, failed = await EInvoiceBackgroundService.send_due_queue_items_once(
            client_factory=_client_factory(first_transport)
        )

        # 前者失敗、後者仍然被送出去了——這正是「逐列隔離」的定義。
        assert (sent, failed) == (1, 1)
        async with factory() as session:
            assert (
                await _queue_row(session, store_a, EInvoiceAction.VOID)
            ).status is UploadStatus.PENDING
            assert (
                await _queue_row(session, store_b, EInvoiceAction.VOID)
            ).status is UploadStatus.UPLOADED
    finally:
        await _truncate()


async def test_scope_guard_is_enforced_under_the_row_lock() -> None:
    """選單是無鎖讀取，界線必須貼在**鎖下**那一行。

    模擬「選中之後、送出之前被改成 F0401」：直接把列的 message_type 改掉再送，
    背景必須拒絕，而不是照著新的型別去打開立端點（Codex 第二輪 P1）。

    **這條守不到的**：它在呼叫前就改好欄位，所以就算守衛被搬回無鎖的 preview 之後、
    取鎖之前，測試一樣會過。要真正釘住「守衛在鎖內」得造出鎖時序，代價遠高於它防的
    情境（要有人在那個毫秒窗口手改資料庫）——單店單機不值得（店主 2026-08-28 裁示）。
    它確實守到的是：型別不符就拒送、而且絕不打開立端點。
    """
    factory = get_sessionmaker()
    try:
        transport = _RecordingTransport()
        async with factory() as session:
            store_id = await _seed_issued_and_voided_sale(session, transport)
            row = await _queue_row(session, store_id, EInvoiceAction.VOID)
            queue_id = row.id
        async with factory() as session:
            await session.execute(
                update(EInvoiceUploadQueue)
                .where(EInvoiceUploadQueue.id == queue_id)
                .values(message_type=EInvoiceMessageType.F0401)
            )
            await session.commit()
        transport.calls.clear()

        async with factory() as session:
            svc = EInvoiceService(session)
            client = await _client_factory(transport)(session, store_id)
            with pytest.raises(EInvoiceQueueNotDroppable):
                await svc.send_via_amego(
                    store_id,
                    queue_id,
                    client=client,
                    allowed_message_types=AUTO_SEND_MESSAGE_TYPES,
                )

        assert "f0401" not in transport.endpoints  # **絕不能真的去開票**
        async with factory() as session:
            still = await session.get(EInvoiceUploadQueue, queue_id)
            assert still is not None and still.status is UploadStatus.PENDING
    finally:
        await _truncate()


async def test_allowance_declares_the_date_it_happened_not_the_send_date() -> None:
    """折讓日必須是**折讓發生那天**，不是我們把它送出去那天。

    自動送出上線前這條踩不到（根本沒人送）；移除年齡上限後，若因平台故障或店休積壓
    了幾週，用送出日會把折讓申報進錯的期別——跨月即申報錯誤（Codex 第四輪 P1）。
    """
    factory = get_sessionmaker()
    try:
        transport = _RecordingTransport()
        async with factory() as session:
            store_id = await _seed_issued_and_voided_sale(session, transport)

        happened_at = datetime.now(UTC) - timedelta(days=40)
        async with factory() as session:
            invoice = await session.scalar(select(Invoice).where(Invoice.store_id == store_id))
            assert invoice is not None
            allowance = InvoiceAllowance(
                store_id=store_id,
                invoice_id=invoice.id,
                net=Decimal(100),
                tax=Decimal(5),
                total=Decimal(105),
            )
            session.add(allowance)
            await session.flush()
            queue_row = EInvoiceUploadQueue(
                store_id=store_id,
                action=EInvoiceAction.ALLOWANCE,
                message_type=EInvoiceMessageType.G0401,
                allowance_id=allowance.id,
                status=UploadStatus.PENDING,
            )
            session.add(queue_row)
            await session.flush()
            await session.execute(
                text("UPDATE invoice_allowances SET created_at = :ts WHERE id = :id"),
                {"ts": happened_at, "id": allowance.id},
            )
            await session.commit()
            queue_id = queue_row.id

        transport.calls.clear()
        transport.forms.clear()
        async with factory() as session:
            svc = EInvoiceService(session)
            client = await _client_factory(transport)(session, store_id)
            await svc.send_via_amego(
                store_id, queue_id, client=client, allowed_message_types=AUTO_SEND_MESSAGE_TYPES
            )
            await session.commit()

        g0401 = next(form for url, form in transport.forms if url.endswith("/json/g0401"))
        payload = json.loads(g0401["data"])[0]
        expected = happened_at.astimezone(ZoneInfo("Asia/Taipei")).date().strftime("%Y%m%d")
        today = datetime.now(UTC).astimezone(ZoneInfo("Asia/Taipei")).date().strftime("%Y%m%d")
        assert expected != today  # 前提成立，否則這條測試沒有鑑別力
        assert payload["AllowanceDate"] == expected
    finally:
        await _truncate()
