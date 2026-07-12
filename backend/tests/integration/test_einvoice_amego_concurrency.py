"""Amego 開立 × 銷售作廢 併發（Codex 第四輪）：全域鎖序 sale→queue，不得死鎖。

真並行（asyncio.gather）兩個獨立 session：POS 自動開立（issue_for_sale，慢傳輸拉長
持鎖窗口）×經理作廢（void_sale）。修正前 issue_for_sale 先鎖佇列列再鎖 sale，與
「作廢先鎖 sale 再動佇列」AB-BA 死鎖；修正後兩者序列化、皆完成：發票收斂
VOID_PENDING（平台已開 → 續 F0501 作廢）、銷售 VOID。
"""

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

import app.core.db as app_db
from app.core.audit import AuditLog
from app.modules.cashdrawer.models import CashMovement, CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.einvoice.amego import AmegoClient
from app.modules.einvoice.models import EInvoiceResultEvent, EInvoiceUploadQueue, Invoice
from app.modules.einvoice.service import EInvoiceService
from app.modules.inventory.models import SerializedItem, StockMovement
from app.modules.inventory.service import InventoryService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.models import Sale, SaleLine, SaleTender
from app.modules.sales.service import SalesService
from app.modules.settings.models import StoreSettings
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    EInvoiceAction,
    Grade,
    InvoiceStatus,
    OwnershipType,
    SaleInvoiceStatus,
    SaleLineType,
    UploadStatus,
    UserRole,
)

_F0401_OK = {
    "code": 0,
    "msg": "",
    "invoice_number": "AB00001111",
    "invoice_time": 1783766130,
    "random_number": "5975",
    "barcode": "11507AB000011115975",
    "qrcode_left": "L",
    "qrcode_right": "R",
}


class _SlowTransport:
    """query 前 sleep 拉長開立的持鎖窗口，讓作廢有時間在中途搶鎖（驗證鎖序）。"""

    async def post_form(self, url: str, form: dict[str, str]) -> dict[str, object]:
        if url.endswith("/json/invoice_query"):
            await asyncio.sleep(0.4)
            return {"code": 71, "msg": "查無資料"}
        return dict(_F0401_OK)


async def _seed_committed(sm: object, *, tag: str) -> tuple[int, int, int]:
    """建店/店員/設定/在庫品/銷售並 commit（雙 session 測試需要真提交）。"""
    async with sm() as s:  # type: ignore[operator]
        store = Store(name=f"併發開立店{tag}", tax_id="12345678")
        s.add(store)
        await s.flush()
        clerk = User(
            store_id=store.id, username=f"amego-cc-{tag}", password_hash="h", role=UserRole.MANAGER
        )
        s.add(clerk)
        await s.flush()
        s.add(StoreSettings(store_id=store.id, einvoice_enabled=True))
        await s.flush()
        await CashDrawerService(s).open_session(store.id, clerk.id, Decimal("1000"))
        item = await InventoryService(s).create_serialized_item(
            store.id,
            item_code=f"SN-CC-{tag}",
            name="相機",
            grade=Grade.A,
            ownership_type=OwnershipType.OWNED,
            listed_price=Decimal(1050),
            acquisition_cost=Decimal(500),
        )
        sale = await SalesService(s).create_sale(
            store.id,
            clerk.id,
            lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=item.item_code)],
        )
        ids = (store.id, clerk.id, sale.id)
        await s.commit()
    return ids


async def _cleanup(sm: object, store_id: int) -> None:
    """清掉本測試真 commit 的整條鏈（其他測試有全域計數斷言/整表清理）。"""
    async with sm() as s4:  # type: ignore[operator]
        for stmt in (
            delete(EInvoiceResultEvent).where(EInvoiceResultEvent.store_id == store_id),
            delete(EInvoiceUploadQueue).where(EInvoiceUploadQueue.store_id == store_id),
            delete(Invoice).where(Invoice.store_id == store_id),
            delete(AuditLog).where(AuditLog.store_id == store_id),
            delete(SaleTender).where(SaleTender.store_id == store_id),
            delete(SaleLine).where(SaleLine.store_id == store_id),
            delete(StockMovement).where(StockMovement.store_id == store_id),
            delete(CashMovement).where(CashMovement.store_id == store_id),
            delete(Sale).where(Sale.store_id == store_id),
            delete(SerializedItem).where(SerializedItem.store_id == store_id),
            delete(CashSession).where(CashSession.store_id == store_id),
            delete(StoreSettings).where(StoreSettings.store_id == store_id),
            delete(User).where(User.store_id == store_id),
            delete(Store).where(Store.id == store_id),
        ):
            await s4.execute(stmt)
        await s4.commit()


async def test_issue_and_void_concurrently_no_deadlock() -> None:
    sm = app_db.get_sessionmaker()
    store_id, clerk_id, sale_id = await _seed_committed(sm, tag="a")

    async def do_issue() -> None:
        async with sm() as s1:
            client = AmegoClient(
                seller_tax_id="12345678",
                app_key="test-key",
                transport=_SlowTransport(),
                base_url="https://invoice-api.amego.tw",
            )
            await EInvoiceService(s1).issue_for_sale(store_id, sale_id, client=client)

    async def do_void() -> None:
        await asyncio.sleep(0.15)  # 開立先持鎖，作廢中途進場
        async with sm() as s2:
            sales = SalesService(s2)
            target = await sales.get_sale_for_update(store_id, sale_id)
            assert target is not None
            await sales.void_sale(target, clerk_id)
            await s2.commit()

    try:
        # 修正前此處死鎖（PG 偵測後 abort 其一 → 例外）；修正後兩者序列化、皆成功。
        await asyncio.gather(do_issue(), do_void())

        async with sm() as s3:
            invoice = await s3.scalar(select(Invoice).where(Invoice.sale_id == sale_id))
            assert invoice is not None
            # 平台已開立、銷售已作廢 → 發票收斂 VOID_PENDING、已排 F0501 續作廢。
            assert invoice.status is InvoiceStatus.VOID_PENDING
            assert invoice.invoice_no == "AB00001111"
            void_items = [
                q
                for q in (
                    await s3.scalars(
                        select(EInvoiceUploadQueue).where(
                            EInvoiceUploadQueue.invoice_id == invoice.id
                        )
                    )
                ).all()
                if q.action is EInvoiceAction.VOID
            ]
            assert len(void_items) == 1
            sale_row = await SalesService(s3).get_sale(store_id, sale_id)
            assert sale_row is not None and sale_row.invoice_status is SaleInvoiceStatus.VOID
    finally:
        await _cleanup(sm, store_id)


class _QueryFoundTransport:
    """對帳查到平台已有此發票（前次未知結果其實成功）；不應再有第二個呼叫。"""

    async def post_form(self, url: str, form: dict[str, str]) -> dict[str, object]:
        assert url.endswith("/json/invoice_query"), f"不應呼叫 {url}"
        return {
            "code": 0,
            "msg": "",
            "data": {
                "invoice_number": "AB00001111",
                "invoice_date": "20260711",
                "invoice_time": "12:34:56",
                "random_number": "5975",
            },
        }


class _QueryNotFoundOnlyTransport:
    """對帳查無（71）；若誤送 f0401 直接斷言失敗（作廢交易不得補開）。"""

    async def post_form(self, url: str, form: dict[str, str]) -> dict[str, object]:
        assert url.endswith("/json/invoice_query"), f"不應呼叫 {url}"
        return {"code": 71, "msg": "查無資料"}


async def test_void_in_claim_gap_still_enqueues_f0501(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """認領 commit → 階段 2 重取鎖的空窗內完成作廢（VOID_PENDING）：成功轉移必須看到
    **刷新後**的發票狀態（expire_on_commit=False 的 identity map 會過期；Codex 第五輪）
    → 走 VOID_PENDING 分支續排 F0501，不得誤走 →ISSUED 漏作廢。
    情境：前次上送結果未知（平台其實已開立）→ 空窗作廢 → 對帳查到 → 補開立欄位
    並續排 F0501。"""
    sm = app_db.get_sessionmaker()
    store_id, clerk_id, sale_id = await _seed_committed(sm, tag="b")

    # hook 掛在**階段 2 取 sale 鎖之前**（＝認領已 commit、主交易尚未持任何鎖的真空窗）；
    # 掛在 lock_queue_item 前會死鎖——那時主交易已持 sale 鎖，作廢在 hook 裡等不到鎖。
    orig_lock = SalesService.lock_sale_row
    state = {"count": 0}

    async def hooked(self: SalesService, store_id_: int, sale_id_: int) -> Sale | None:
        state["count"] += 1
        if state["count"] == 2:  # 第 1 次＝階段 1；第 2 次＝階段 2 重取鎖前（空窗）
            async with sm() as s2:
                sales = SalesService(s2)
                target = await sales.get_sale_for_update(store_id, sale_id)
                assert target is not None
                await sales.void_sale(target, clerk_id)
                await s2.commit()
        return await orig_lock(self, store_id_, sale_id_)

    monkeypatch.setattr(SalesService, "lock_sale_row", hooked)

    try:
        async with sm() as s1:
            client = AmegoClient(
                seller_tax_id="12345678",
                app_key="test-key",
                transport=_QueryFoundTransport(),
                base_url="https://invoice-api.amego.tw",
            )
            svc = EInvoiceService(s1)
            queue_id = next(
                i.id
                for i in await svc.list_queue(store_id)
                if i.action is EInvoiceAction.ISSUE
            )
            await svc.send_via_amego(store_id, queue_id, client=client)

        async with sm() as s3:
            invoice = await s3.scalar(select(Invoice).where(Invoice.sale_id == sale_id))
            assert invoice is not None
            assert invoice.status is InvoiceStatus.VOID_PENDING  # 不得誤標 ISSUED
            assert invoice.invoice_no == "AB00001111"  # 平台已開 → 字軌照填
            void_items = [
                q
                for q in (
                    await s3.scalars(
                        select(EInvoiceUploadQueue).where(
                            EInvoiceUploadQueue.invoice_id == invoice.id
                        )
                    )
                ).all()
                if q.action is EInvoiceAction.VOID
            ]
            assert len(void_items) == 1  # F0501 已排（續作廢）
    finally:
        await _cleanup(sm, store_id)


async def test_void_in_claim_gap_with_platform_not_found_cancels_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空窗作廢＋平台**明確查無**（71）→ **取消開立**（Codex 第八輪）：作廢交易不得
    再送 f0401 產生真實發票——佇列 CANCELLED、發票收斂 VOID、零 f0401 呼叫。"""
    sm = app_db.get_sessionmaker()
    store_id, clerk_id, sale_id = await _seed_committed(sm, tag="c")

    orig_lock = SalesService.lock_sale_row
    state = {"count": 0}

    async def hooked(self: SalesService, store_id_: int, sale_id_: int) -> Sale | None:
        state["count"] += 1
        if state["count"] == 2:
            async with sm() as s2:
                sales = SalesService(s2)
                target = await sales.get_sale_for_update(store_id, sale_id)
                assert target is not None
                await sales.void_sale(target, clerk_id)
                await s2.commit()
        return await orig_lock(self, store_id_, sale_id_)

    monkeypatch.setattr(SalesService, "lock_sale_row", hooked)

    try:
        async with sm() as s1:
            client = AmegoClient(
                seller_tax_id="12345678",
                app_key="test-key",
                transport=_QueryNotFoundOnlyTransport(),  # 誤送 f0401 會在替身內斷言失敗
                base_url="https://invoice-api.amego.tw",
            )
            svc = EInvoiceService(s1)
            queue_id = next(
                i.id
                for i in await svc.list_queue(store_id)
                if i.action is EInvoiceAction.ISSUE
            )
            item = await svc.send_via_amego(store_id, queue_id, client=client)
            assert item.status is UploadStatus.CANCELLED

        async with sm() as s3:
            invoice = await s3.scalar(select(Invoice).where(Invoice.sale_id == sale_id))
            assert invoice is not None
            assert invoice.status is InvoiceStatus.VOID  # 平台沒收過 → 直接收斂 VOID
            assert invoice.invoice_no is None  # 從未開立
            void_items = [
                q
                for q in (
                    await s3.scalars(
                        select(EInvoiceUploadQueue).where(
                            EInvoiceUploadQueue.invoice_id == invoice.id
                        )
                    )
                ).all()
                if q.action is EInvoiceAction.VOID
            ]
            assert void_items == []  # 無需 F0501（平台無發票可作廢）
    finally:
        await _cleanup(sm, store_id)



async def test_settings_patch_serialized_with_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """並發：結帳（expected_einvoice_enabled=True）× PATCH 關閉電子發票（Codex 第廿三輪）。

    交易級 advisory lock 使兩者對同店序列化：以 hook 讓結帳在**取設定鎖後**暫停，其間
    發動 PATCH——PATCH 卡在同一鎖上直到結帳 commit。驗證：結帳以啟用態成立
    （PENDING_ISSUE），PATCH 被序列化在其後才生效（commit 順序：結帳先於 PATCH）。
    """
    from app.modules.settings.schemas import SettingsUpdateRequest
    from app.modules.settings.service import StoreSettingsService

    sm = app_db.get_sessionmaker()
    async with sm() as s0:
        store = Store(name="設定並發店", tax_id="12345678")
        s0.add(store)
        await s0.flush()
        clerk = User(
            store_id=store.id, username="cc-settings", password_hash="h", role=UserRole.MANAGER
        )
        s0.add(clerk)
        await s0.flush()
        s0.add(StoreSettings(store_id=store.id, einvoice_enabled=True))
        await s0.flush()
        await CashDrawerService(s0).open_session(store.id, clerk.id, Decimal("1000"))
        item = await InventoryService(s0).create_serialized_item(
            store.id,
            item_code="SN-SET-1",
            name="相機",
            grade=Grade.A,
            ownership_type=OwnershipType.OWNED,
            listed_price=Decimal(1050),
            acquisition_cost=Decimal(500),
        )
        store_id, clerk_id, item_code = store.id, clerk.id, item.item_code
        await s0.commit()

    orig_lock = StoreSettingsService.lock_store
    checkout_has_lock = asyncio.Event()
    release = asyncio.Event()
    commit_order: list[str] = []

    async def hooked_lock(self: StoreSettingsService, store_id_: int) -> None:
        await orig_lock(self, store_id_)  # 結帳先取鎖
        checkout_has_lock.set()
        await release.wait()  # 持鎖暫停，讓 PATCH 有機會嘗試（並卡在鎖上）

    monkeypatch.setattr(StoreSettingsService, "lock_store", hooked_lock)

    async def do_checkout() -> str:
        async with sm() as s1:
            sale = await SalesService(s1).create_sale(
                store_id,
                clerk_id,
                lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=item_code)],
                expected_einvoice_enabled=True,
            )
            status = sale.invoice_status.value
            await s1.commit()
            commit_order.append("checkout")
            return status

    async def do_patch() -> None:
        await checkout_has_lock.wait()  # 等結帳已持鎖
        await asyncio.sleep(0.2)  # 讓 PATCH 真正卡在 advisory lock 上
        release.set()  # 放行結帳（結帳 commit→釋放鎖→PATCH 才取得鎖）
        async with sm() as s2:
            await StoreSettingsService(s2).update_settings(
                store_id,
                actor_user_id=clerk_id,
                patch=SettingsUpdateRequest(einvoice_enabled=False),
            )
            await s2.commit()
            commit_order.append("patch")

    try:
        status_value, _ = await asyncio.gather(do_checkout(), do_patch())
        assert status_value == "PENDING_ISSUE"  # 結帳以啟用態成立
        assert commit_order == ["checkout", "patch"]  # 序列化：結帳先於 PATCH
        async with sm() as s3:
            invoices = (
                await s3.scalars(select(Invoice).where(Invoice.store_id == store_id))
            ).all()
            assert len(invoices) == 1  # 啟用態建了 PENDING 發票
            settings = await StoreSettingsService(s3).get_effective_settings(store_id)
            assert settings.einvoice_enabled is False  # PATCH 最終生效（在結帳之後）
    finally:
        await _cleanup(sm, store_id)
