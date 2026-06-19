"""sales 業務邏輯：POS 結帳的單交易 orchestrate。

於單一 DB 交易內協調 inventory / cashdrawer / consignment / settings 四個 service：
扣庫存（用 T5 原子機制，不先查再改）、寫 stock_movement(OUT)、收現 cash_movement(SALE_IN)、
總額層級推稅一次、寄售品建 PENDING 結算。任一步失敗整筆回復（本層只 flush、不 commit；
commit/rollback 由呼叫端控制），不留「庫存扣了但現金沒進」之類的半套。跨模組一律經對方 service。

守門：
- 收現必須在開帳中的 cash_session 下（§7.8），無開帳 → 整筆擋（最先檢查，未動任何庫存）。
- 序號品以原子轉移 IN_STOCK→SOLD 為售出保證（已 SOLD 不可再賣、併發只成功一筆）。
- 散裝 remaining_qty / 數量品 quantity_on_hand 以條件式 UPDATE 原子扣減，不得 < 0。
金額一律 Decimal/整數元（core/money），稅於發票總額層級推算一次（不逐項算稅）。
"""

import hashlib
import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.money import split_tax_inclusive
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.service import ConsignmentService
from app.modules.contacts.service import ContactService
from app.modules.inventory.service import InventoryService
from app.modules.sales.inputs import SaleLineInput, TenderInput
from app.modules.sales.models import Sale, SaleLine, SaleTender
from app.modules.sales.repository import SalesRepository
from app.modules.settings.service import StoreSettingsService
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.service import UserService
from app.shared.enums import (
    CashMovementType,
    ItemKind,
    OwnershipType,
    PaymentMethod,
    SaleInvoiceStatus,
    SaleLineType,
    StockReason,
    StoreCreditSourceType,
    TenderType,
)
from app.shared.exceptions import (
    CrossStoreReference,
    EmptySale,
    IdempotencyKeyConflict,
    InvalidSaleTender,
    NoOpenCashSession,
    SaleAlreadyVoid,
    SaleItemNotFound,
    SaleLineInvalid,
)

_POINTS_DIVISOR = Decimal(100)  # 會員點數：floor(含稅總額 ÷ 100)，docs/16 §0


def _member_points_for(total: Decimal) -> int:
    """該筆銷售累積的會員點數（floor；total 為含稅整數元，與 tender 組成無關）。"""
    return int(total // _POINTS_DIVISOR)


def _cart_fingerprint(
    lines: list[SaleLineInput],
    buyer_contact_id: int | None,
    tenders: list[TenderInput] | None = None,
) -> str:
    """購物車＋收款組成的穩定 sha256；供 idempotency 重播時比對請求是否相同。

    tenders 納入指紋：同 key 但收款組成不同（影響現金/帳本副作用）→ 視為不同請求。
    """
    canonical = {
        "buyer_contact_id": buyer_contact_id,
        "lines": [
            {
                "line_type": line.line_type.value,
                "item_code": line.item_code,
                "catalog_product_id": line.catalog_product_id,
                "bulk_lot_id": line.bulk_lot_id,
                "qty": line.qty,
            }
            for line in lines
        ],
        # 收款組成納入指紋，但對 tender_type 正規化排序（順序不影響語意：每型別至多一筆）。
        "tenders": (
            None
            if tenders is None
            else sorted(
                ({"tender_type": t.tender_type.value, "amount": str(t.amount)} for t in tenders),
                key=lambda d: d["tender_type"],
            )
        ),
    }
    payload = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SalesService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = SalesRepository(session)
        self._inventory = InventoryService(session)
        self._cash = CashDrawerService(session)
        self._consignment = ConsignmentService(session)
        self._settings = StoreSettingsService(session)
        self._users = UserService(session)
        self._contacts = ContactService(session)
        self._storecredit = StoreCreditService(session)

    @staticmethod
    def _normalize_tenders(tenders: list[TenderInput] | None) -> list[TenderInput] | None:
        """service 邊界守衛（直呼/raw 也擋）：非空、型別不重複、金額正整數。

        省略（None）→ 維持 None，由 _resolve_tenders 預設單一 CASH 全額。
        """
        if tenders is None:
            return None
        if not tenders:
            raise InvalidSaleTender("tenders 不可為空陣列（省略才表示預設現金全額）")
        seen: set[TenderType] = set()
        for t in tenders:
            if t.tender_type in seen:
                raise InvalidSaleTender(f"收款型別 {t.tender_type.value} 重複，每種至多一筆")
            seen.add(t.tender_type)
            if t.amount != t.amount.to_integral_value():
                raise InvalidSaleTender("收款金額必須為整數元")
            if t.amount <= 0:
                raise InvalidSaleTender("收款金額必須為正")
        return tenders

    @staticmethod
    def _resolve_tenders(total: Decimal, tenders: list[TenderInput] | None) -> list[TenderInput]:
        """把收款計畫對齊 total：省略 → 單一 CASH 全額；提供 → Σ amount 必須等於 total。"""
        if tenders is None:
            return [TenderInput(tender_type=TenderType.CASH, amount=total)]
        paid = sum((t.amount for t in tenders), Decimal(0))
        if paid != total:
            raise InvalidSaleTender(f"收款總額（{paid}）必須等於應付總額（{total}）")
        return tenders

    @staticmethod
    def _summary_payment_method(plan: list[TenderInput]) -> PaymentMethod:
        """sales.payment_method 摘要：單一 tender → 該型別、多 tender → MIXED。"""
        if len(plan) == 1:
            return PaymentMethod(plan[0].tender_type.value)
        return PaymentMethod.MIXED

    async def create_sale(
        self,
        store_id: int,
        clerk_user_id: int,
        *,
        lines: list[SaleLineInput],
        buyer_contact_id: int | None = None,
        tenders: list[TenderInput] | None = None,
        idempotency_key: str | None = None,
    ) -> Sale:
        """建立銷售單並完成扣庫存/收款/結算；任一步失敗整筆回復（不 commit）。

        收款（SC-3，docs/16 §3.2）：tenders 省略 → 單一 CASH 全額（向後相容）；提供時
        Σ amount 必須等於 total。CASH tender → 錢櫃 SALE_IN（現金部分）；STORE_CREDIT
        tender → 帳本 DEBIT（買方購物金，餘額不足 → InsufficientStoreCredit 整筆回滾）。

        idempotency（D-2）：帶 idempotency_key 時，若同 (store_id, key) 已有銷售 → 直接回原單、
        不重跑任何副作用（防網路重試重複建單/收錢）。並行重送的競態由 sales 的
        (store_id, idempotency_key) 唯一約束在 flush/commit 擋下，由呼叫端據此回原單。
        """
        if not lines:
            raise EmptySale("銷售單必須至少有一筆明細")

        normalized_tenders = self._normalize_tenders(tenders)
        fingerprint = _cart_fingerprint(lines, buyer_contact_id, normalized_tenders)

        # idempotent replay：已存在同 key 的銷售 → 內容相同回原單、不再產生副作用；
        # 內容不同則拒絕（避免誤用/重用 key 把不同購物車的結帳靜默丟掉）。
        if idempotency_key is not None:
            replay = await self.find_idempotent_replay(
                store_id,
                idempotency_key,
                lines=lines,
                buyer_contact_id=buyer_contact_id,
                tenders=normalized_tenders,
            )
            if replay is not None:
                return replay

        has_cash = normalized_tenders is None or any(
            t.tender_type == TenderType.CASH for t in normalized_tenders
        )
        has_store_credit = normalized_tenders is not None and any(
            t.tender_type == TenderType.STORE_CREDIT for t in normalized_tenders
        )
        # 購物金付款必須有買方（扣誰的購物金）；於動任何庫存前就擋（§3.2、I-8）。
        if has_store_credit and buyer_contact_id is None:
            raise InvalidSaleTender("以購物金付款必須指定買方會員（buyer_contact_id）")
        # 收現必須在開帳中（§7.8）：最先檢查，避免動了庫存才發現不能收錢。純購物金
        # 付款不碰現金（I-9），不要求開帳。
        if has_cash and await self._cash.get_current_session(store_id) is None:
            raise NoOpenCashSession("結帳收現必須在開帳中的 cash_session 下進行，請先開帳")

        # 多分店資料隔離（§4）：clerk 與 buyer 都必須屬於本店，擋下跨店引用。
        if await self._users.get_user_in_store(store_id, clerk_user_id) is None:
            raise CrossStoreReference(f"clerk {clerk_user_id} 不屬於 store {store_id}")
        if buyer_contact_id is not None and (
            await self._contacts.get_contact(store_id, buyer_contact_id) is None
        ):
            raise CrossStoreReference(f"buyer contact {buyer_contact_id} 不屬於 store {store_id}")

        sale = await self._repo.add_sale(
            Sale(
                store_id=store_id,
                idempotency_key=idempotency_key,
                idempotency_fingerprint=fingerprint,
                clerk_user_id=clerk_user_id,
                buyer_contact_id=buyer_contact_id,
                subtotal=Decimal(0),
                tax=Decimal(0),
                total=Decimal(0),
            )
        )

        total = Decimal(0)
        # 售出的寄售品 → 稍後建 PENDING 結算：(序號品 id, 售價, 抽成百分數)
        consignment_sales: list[tuple[int, Decimal, int]] = []

        # 並發鎖序：先依 id 升冪鎖定本單序號品列，與作廢的 id 序退場一致；之後逐行（購物車序）
        # 的 sell 只再觸碰已持有的鎖。避免多件、反序銷售與並行作廢互卡 AB-BA 死結（Codex F6.5）。
        serialized_codes = [
            line.item_code
            for line in lines
            if line.line_type == SaleLineType.SERIALIZED and line.item_code is not None
        ]
        await self._inventory.prelock_serialized_for_sale(store_id, serialized_codes)

        for line in lines:
            line_total = await self._process_line(store_id, sale.id, line, consignment_sales)
            total += line_total

        # 零/負總額拒（§6 金額為正整數元）：每筆 tender 金額須 >0（DB CHECK），零總額會
        # 落到「無收款腿或 amount=0」的不合法狀態；免費出貨應走獨立流程，不借道銷售。
        if total <= 0:
            raise InvalidSaleTender("銷售總額必須大於 0（免費出貨請走獨立流程）")

        # 收款計畫對齊 total（Σ tenders 必須 = total，否則 422）。
        plan = self._resolve_tenders(total, normalized_tenders)

        # 稅於發票總額層級推算一次（§6）；不逐項算稅。
        tax_rate = (await self._settings.get_effective_settings(store_id)).tax_rate
        net, tax = split_tax_inclusive(total, tax_rate)
        sale.subtotal = Decimal(net)
        sale.tax = Decimal(tax)
        sale.total = total
        sale.payment_method = self._summary_payment_method(plan)
        await self._session.flush()

        # 收款副作用（§3.2）：現金 tender → 錢櫃 SALE_IN（現金部分，非全額）；
        # 購物金 tender → 帳本 DEBIT（買方）。發票/稅/點數不受 tender 組成影響。
        await self._apply_tenders(store_id, sale, plan, clerk_user_id, buyer_contact_id)

        # 寄售品 → 建 PENDING 結算（店家收入只認抽成，§7.3）。
        for serialized_item_id, gross, commission_pct in consignment_sales:
            await self._consignment.create_settlement(
                store_id,
                serialized_item_id=serialized_item_id,
                sale_id=sale.id,
                gross=gross,
                commission_pct=commission_pct,
            )

        # 會員點數累積（docs/16 §0）：floor(total/100)，同交易內、與銷售同生共死；
        # 無買方不計；冪等重送走 find_idempotent_replay 回原單、不會重複累積。
        # 實際累積數記在 sale.awarded_points：void 以此沖回、不重算（規則改版/歷史單不錯沖）。
        if buyer_contact_id is not None:
            sale.awarded_points = _member_points_for(total)
            await self._contacts.add_member_points(store_id, buyer_contact_id, sale.awarded_points)

        await self._session.flush()
        return sale

    async def _apply_tenders(
        self,
        store_id: int,
        sale: Sale,
        plan: list[TenderInput],
        clerk_user_id: int,
        buyer_contact_id: int | None,
    ) -> None:
        """落地收款：現金部分入錢櫃 SALE_IN、購物金部分扣帳本 DEBIT，並記 sale_tenders。

        固定 CASH 先於 STORE_CREDIT 落地：建立 cash_session 與 store_credit_account 的**全域唯一
        鎖序**（與收購作廢的 cash→credit 一致），避免「購物金-先的混合銷售」與並行 SPLIT 作廢在同一
        contact 形成 AB-BA 死結（Codex F6.5 高風險）。各 tender 金額已固定、改順序不影響金額/紀錄。
        """
        for tender in sorted(plan, key=lambda t: 0 if t.tender_type == TenderType.CASH else 1):
            if tender.tender_type == TenderType.CASH:
                await self._cash.record_movement(
                    store_id,
                    CashMovementType.SALE_IN,
                    tender.amount,
                    actor_user_id=clerk_user_id,
                    ref_type="sale",
                    ref_id=sale.id,
                )
            else:  # STORE_CREDIT：扣買方購物金（餘額不足 → InsufficientStoreCredit）
                assert buyer_contact_id is not None  # 上方已於購物金付款時強制買方存在
                await self._storecredit.debit(
                    store_id,
                    buyer_contact_id,
                    amount=tender.amount,
                    source_type=StoreCreditSourceType.SALE,
                    source_id=sale.id,
                    created_by=clerk_user_id,
                )
            await self._repo.add_tender(
                SaleTender(
                    store_id=store_id,
                    sale_id=sale.id,
                    tender_type=tender.tender_type,
                    amount=tender.amount,
                )
            )

    async def find_idempotent_replay(
        self,
        store_id: int,
        idempotency_key: str,
        *,
        lines: list[SaleLineInput],
        buyer_contact_id: int | None,
        tenders: list[TenderInput] | None = None,
    ) -> Sale | None:
        """同 key 且購物車＋收款相符 → 回原單；內容不符 → IdempotencyKeyConflict；不存在 → None。

        pre-check（create_sale）與 router 的 IntegrityError handler（並行重送）共用此處，
        避免「修一條路徑、漏另一條」導致併發同 key 不同購物車仍被靜默當成功。
        """
        existing = await self._repo.get_by_idempotency_key(store_id, idempotency_key)
        if existing is None:
            return None
        if existing.idempotency_fingerprint != _cart_fingerprint(lines, buyer_contact_id, tenders):
            raise IdempotencyKeyConflict(
                f"idempotency key 已用於不同的購物車內容（sale {existing.id}）"
            )
        return existing

    # ── 查詢 ──
    async def get_sale(self, store_id: int, sale_id: int) -> Sale | None:
        return await self._repo.get_sale(store_id, sale_id)

    async def get_lines(self, sale_id: int) -> list[SaleLine]:
        return await self._repo.list_lines(sale_id)

    async def get_tenders(self, sale_id: int) -> list[SaleTender]:
        return await self._repo.list_tenders(sale_id)

    async def list_sales(
        self,
        store_id: int,
        *,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
        offset: int,
    ) -> list[Sale]:
        return await self._repo.list_sales(
            store_id, date_from=date_from, date_to=date_to, limit=limit, offset=offset
        )

    async def list_purchases_by_buyer(
        self,
        store_id: int,
        contact_id: int,
        *,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Sale]:
        """某會員的消費紀錄（會員中心；store 範圍、可選日期區間、新到舊、分頁；docs/17 §5.2）。"""
        return await self._repo.list_sales_by_buyer(
            store_id,
            contact_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )

    async def count_purchases_by_buyer(self, store_id: int, contact_id: int) -> int:
        """某會員的消費總筆數（會員中心 overview headline）。"""
        return await self._repo.count_sales_by_buyer(store_id, contact_id)

    async def line_counts_for_sales(self, sale_ids: list[int]) -> dict[int, int]:
        """各銷售單明細行數（會員中心消費清單；單一查詢避免 N+1）。"""
        return await self._repo.count_lines_for_sales(sale_ids)

    # ── SC-5b §5B 唯讀彙總（供 storecredit 指標/引擎跨模組取數，§2 經 service）──

    async def period_margin(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> dict[str, Decimal]:
        """期間毛利拆解：revenue（商品收入）、buyout_margin（買斷毛利）、寄售抽成。

        買斷毛利＋商品收入由自有/寄售商品行推導（sales 經 inventory 成本，repo 唯讀 join）；
        寄售抽成經 consignment service 依未作廢 sale_id 取（§2）。m = (買斷毛利＋抽成) ÷ 收入。
        """
        buyout_margin, revenue = await self._repo.goods_margin_and_revenue(
            store_id, date_from, date_to
        )
        sale_ids = await self._repo.nonvoid_sale_ids(store_id, date_from, date_to)
        commission = await self._consignment.commission_total_for_sales(store_id, sale_ids)
        return {
            "revenue": revenue,
            "buyout_margin": buyout_margin,
            "consignment_commission": commission,
        }

    async def excess_spend_components(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> dict[str, Decimal]:
        """含購物金 tender 的銷售：total（Σ含稅總額）、cash（Σ現金部分）；率=cash÷total。"""
        total, cash = await self._repo.excess_spend_components(store_id, date_from, date_to)
        return {"total": total, "cash": cash}

    async def member_purchase_count(
        self, store_id: int, contact_id: int, *, date_from: datetime, date_to: datetime
    ) -> int:
        """某會員在 [date_from, date_to) 的未作廢消費筆數（α 代理：CREDIT 入帳前 N 天消費紀錄）。"""
        return await self._repo.member_purchase_count(store_id, contact_id, date_from, date_to)

    # ── 作廢 ──
    async def void_sale(self, sale: Sale, actor_user_id: int) -> Sale:
        """作廢銷售：標記 invoice_status=VOID（待作廢），寫稽核；不刪除、不在此反轉庫存/退現。

        若原銷售已開發票（invoice_status=ISSUED），此 VOID 為「作廢發票流程」的接縫——實際
        電子發票作廢 XML 由 T13/T14 處理。退現/折讓/庫存回補屬 Phase 4 returns（§7.5），不在此；
        但**寄售結算反轉**於此一併處理（invariant #7：未付→CANCELLED、已付→reclaim_needed），
        否則作廢後該結算仍 PENDING、可被付款給寄售人造成現金漏出（Codex adversarial round-2）。

        併發保證：先以 FOR UPDATE 鎖 sale 列並刷新到已提交狀態，再檢查/轉移（比照 D-1）；
        兩個並行作廢只一個成功，另一個鎖後見 VOID → SaleAlreadyVoid，稽核也只寫一筆。
        """
        locked = await self._repo.lock_sale(sale.store_id, sale.id)
        if locked is None or locked.invoice_status == SaleInvoiceStatus.VOID:
            raise SaleAlreadyVoid(f"sale {sale.id} 已作廢，不可重複作廢")
        sale = locked
        before = sale.invoice_status.value
        sale.invoice_status = SaleInvoiceStatus.VOID
        await self._session.flush()
        await write_audit_log(
            self._session,
            store_id=sale.store_id,
            actor_user_id=actor_user_id,
            action="VOID_SALE",
            entity_type="sale",
            entity_id=str(sale.id),
            before={"invoice_status": before},
            after={"invoice_status": SaleInvoiceStatus.VOID.value},
        )
        # 作廢沖回該筆「當時實際累積」的點數（awarded_points；歷史單為 0 → 不倒扣）。
        if sale.buyer_contact_id is not None and sale.awarded_points > 0:
            await self._contacts.add_member_points(
                sale.store_id, sale.buyer_contact_id, -sale.awarded_points
            )
        # 購物金 tender 沖回（§3.3）：DEBIT/SALE → REVERSAL/SALE_VOID（入回買方餘額）；
        # 無購物金 tender → no-op。沖正冪等：重複作廢路徑被 SaleAlreadyVoid 先擋，
        # 即便走到也只入回一次（同來源回原沖正列）。現金 tender 退現屬 Phase 4 returns。
        await self._storecredit.reverse_for_sale_void(
            sale.store_id, sale.id, created_by=actor_user_id
        )
        # 寄售結算反轉（invariant #7，Phase 4）：未付→CANCELLED、已付→reclaim_needed，
        # 否則作廢後仍可付款給寄售人造成現金漏出（Codex adversarial）。非寄售單 → no-op。
        await self._consignment.cancel_settlements_for_sale(
            sale.store_id, sale.id, actor_user_id=actor_user_id
        )
        return sale

    async def record_print_detail(self, sale: Sale, actor_user_id: int) -> None:
        """補印商品明細聯：寫稽核（實際列印由前端送硬體代理，見 docs/04、Phase 3 硬體）。"""
        await write_audit_log(
            self._session,
            store_id=sale.store_id,
            actor_user_id=actor_user_id,
            action="PRINT_SALE_DETAIL",
            entity_type="sale",
            entity_id=str(sale.id),
        )

    async def _process_line(
        self,
        store_id: int,
        sale_id: int,
        line: SaleLineInput,
        consignment_sales: list[tuple[int, Decimal, int]],
    ) -> Decimal:
        """解析單行、原子扣庫存、寫 stock_movement(OUT)、建 sale_line；回傳該行含稅小計。"""
        if line.line_type == SaleLineType.SERIALIZED:
            return await self._process_serialized(store_id, sale_id, line, consignment_sales)
        if line.line_type == SaleLineType.CATALOG:
            return await self._process_catalog(store_id, sale_id, line)
        return await self._process_bulk(store_id, sale_id, line)

    async def _process_serialized(
        self,
        store_id: int,
        sale_id: int,
        line: SaleLineInput,
        consignment_sales: list[tuple[int, Decimal, int]],
    ) -> Decimal:
        if line.item_code is None:
            raise SaleLineInvalid("SERIALIZED 明細必須帶 item_code")
        if line.qty != 1:
            raise SaleLineInvalid("SERIALIZED 明細數量必須為 1")
        item = await self._inventory.get_serialized_by_code(store_id, line.item_code)
        if item is None:
            raise SaleItemNotFound(f"找不到序號品 {line.item_code}")
        line_total = item.listed_price  # 序號品 qty 固定 1
        # 原子轉移 IN_STOCK→SOLD（已售出/併發競態 → 拋 InvalidStateTransition）。
        await self._inventory.sell_serialized_item(item.id)
        await self._inventory.record_stock_out(
            store_id,
            ItemKind.SERIALIZED,
            qty=1,
            reason=StockReason.SALE,
            ref_type="sale",
            ref_id=sale_id,
            serialized_item_id=item.id,
        )
        await self._repo.add_line(
            SaleLine(
                store_id=store_id,
                sale_id=sale_id,
                line_type=SaleLineType.SERIALIZED,
                serialized_item_id=item.id,
                description=item.name,
                qty=1,
                unit_price=item.listed_price,
                line_total=line_total,
            )
        )
        if item.ownership_type == OwnershipType.CONSIGNMENT:
            # 寄售品建檔時保證有 commission_pct（inventory 已驗），此處防呆。
            if item.commission_pct is None:
                raise SaleLineInvalid(f"寄售品 {line.item_code} 缺 commission_pct")
            consignment_sales.append((item.id, line_total, item.commission_pct))
        return line_total

    async def _process_catalog(self, store_id: int, sale_id: int, line: SaleLineInput) -> Decimal:
        if line.catalog_product_id is None:
            raise SaleLineInvalid("CATALOG 明細必須帶 catalog_product_id")
        if line.qty <= 0:
            raise SaleLineInvalid("CATALOG 明細數量必須 > 0")
        product = await self._inventory.get_catalog(store_id, line.catalog_product_id)
        if product is None:
            raise SaleItemNotFound(f"找不到數量型商品 {line.catalog_product_id}")
        line_total = product.unit_price * line.qty
        await self._inventory.sell_catalog_items(product.id, line.qty)
        await self._inventory.record_stock_out(
            store_id,
            ItemKind.CATALOG,
            qty=line.qty,
            reason=StockReason.SALE,
            ref_type="sale",
            ref_id=sale_id,
            catalog_product_id=product.id,
        )
        await self._repo.add_line(
            SaleLine(
                store_id=store_id,
                sale_id=sale_id,
                line_type=SaleLineType.CATALOG,
                catalog_product_id=product.id,
                description=product.name,
                qty=line.qty,
                unit_price=product.unit_price,
                line_total=line_total,
            )
        )
        return line_total

    async def _process_bulk(self, store_id: int, sale_id: int, line: SaleLineInput) -> Decimal:
        if line.bulk_lot_id is None:
            raise SaleLineInvalid("BULK_LOT 明細必須帶 bulk_lot_id")
        if line.qty <= 0:
            raise SaleLineInvalid("BULK_LOT 明細數量必須 > 0")
        lot = await self._inventory.get_bulk_lot(store_id, line.bulk_lot_id)
        if lot is None:
            raise SaleItemNotFound(f"找不到散裝批 {line.bulk_lot_id}")
        line_total = lot.unit_price * line.qty
        # 原子扣減 remaining_qty（不足 → InsufficientStock；歸零自動轉 SOLD_OUT）。
        await self._inventory.sell_bulk_lot_items(lot.id, line.qty)
        await self._inventory.record_stock_out(
            store_id,
            ItemKind.BULK_LOT,
            qty=line.qty,
            reason=StockReason.SALE,
            ref_type="sale",
            ref_id=sale_id,
            bulk_lot_id=lot.id,
        )
        await self._repo.add_line(
            SaleLine(
                store_id=store_id,
                sale_id=sale_id,
                line_type=SaleLineType.BULK_LOT,
                bulk_lot_id=lot.id,
                description=lot.name,
                qty=line.qty,
                unit_price=lot.unit_price,
                line_total=line_total,
            )
        )
        return line_total
