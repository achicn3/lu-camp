"""returns 業務邏輯：建立退貨、退現、回補庫存、更新銷售狀態。"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.service import ConsignmentService
from app.modules.einvoice.service import EInvoiceService
from app.modules.inventory.service import InventoryService
from app.modules.returns.models import CustomerReturn, ReturnLine
from app.modules.returns.repository import ReturnsMarginAdjustments, ReturnsRepository
from app.modules.sales.models import SaleLine, SaleTender
from app.modules.sales.repository import SalesRepository
from app.modules.settings.service import StoreSettingsService
from app.shared.enums import (
    CashMovementType,
    InvoiceStatus,
    PaymentMethod,
    SaleInvoiceStatus,
    SaleLineType,
    SaleStatus,
    TenderType,
)
from app.shared.exceptions import (
    IdempotencyKeyConflict,
    ReturnConflict,
    ReturnLineInvalid,
    ReturnNotFound,
    ReturnSaleNotFound,
)


@dataclass(frozen=True)
class ReturnLineInput:
    sale_line_id: int
    qty: int


def _return_fingerprint(sale_id: int, requested: dict[int, int], reason: str) -> str:
    """退貨請求的穩定 sha256（sale + 明細 + 原因）；同 key 重送時比對請求是否相同。"""
    canonical = {
        "sale_id": sale_id,
        "reason": reason,
        "lines": sorted(
            ({"sale_line_id": k, "qty": v} for k, v in requested.items()),
            key=lambda d: d["sale_line_id"],
        ),
    }
    payload = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ReturnsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ReturnsRepository(session)
        self._sales = SalesRepository(session)
        self._inventory = InventoryService(session)
        self._cash = CashDrawerService(session)
        self._consignment = ConsignmentService(session)
        self._einvoice = EInvoiceService(session)
        self._settings = StoreSettingsService(session)

    async def get_return(self, store_id: int, return_id: int) -> CustomerReturn | None:
        return await self._repo.get_return(store_id, return_id)

    async def margin_adjustments(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> "ReturnsMarginAdjustments":
        """期間退貨的毛利扣減（D-8(1)；供 sales.margin_breakdown 同源扣除，read-only）。"""
        return await self._repo.margin_adjustments(store_id, date_from, date_to)

    async def returned_qty_for_sale(self, store_id: int, sale_id: int) -> dict[int, int]:
        """該銷售各明細已退貨累積量（退貨頁算可退餘量用，read-only）。"""
        sale_lines = await self._sales.list_lines(sale_id)
        return await self._repo.returned_qty_by_sale_line_ids(
            store_id, [line.id for line in sale_lines]
        )

    async def has_returns_for_sale(self, store_id: int, sale_id: int) -> bool:
        """該銷售是否已有退貨（供 sales.void_sale 前置檢查：已退貨者不可作廢）。"""
        return await self._repo.has_returns_for_sale(store_id, sale_id)

    async def find_idempotent_replay(
        self,
        store_id: int,
        idempotency_key: str,
        *,
        sale_id: int,
        requested: dict[int, int],
        reason: str,
    ) -> CustomerReturn | None:
        """同 key 且請求相符 → 回原退貨單；內容不符 → IdempotencyKeyConflict；不存在 → None。

        pre-check（create_return）與 router 的 IntegrityError handler（並行重送）共用此處。
        """
        existing = await self._repo.get_by_idempotency_key(store_id, idempotency_key)
        if existing is None:
            return None
        if existing.idempotency_fingerprint != _return_fingerprint(sale_id, requested, reason):
            raise IdempotencyKeyConflict(
                f"idempotency key 已用於不同的退貨內容（return {existing.id}）"
            )
        return existing

    async def create_return(
        self,
        store_id: int,
        *,
        sale_id: int,
        lines: Sequence[ReturnLineInput],
        reason: str,
        actor_user_id: int,
        idempotency_key: str,
    ) -> CustomerReturn:
        """建立退貨單並執行副作用；成功前只 flush，不 commit。

        Phase 4B v1 支援純現金銷售的 catalog / serialized / bulk 退貨。行數量驗證以
        既有 return_lines 聚合防重複退；sale 列以 FOR UPDATE 鎖住，避免並行重退同一張單。

        idempotency：同 (store_id, idempotency_key) 已有退貨 → 直接回原單、不重跑任何副作用
        （防雙擊/網路重試重複退現）。並行重送的競態由 (store_id, idempotency_key) 唯一約束在
        flush 擋下，由呼叫端據此回原單（比照 sales D-2）。
        """
        clean_reason = reason.strip()
        if clean_reason == "":
            raise ReturnLineInvalid("退貨原因不可空白")
        requested = self._normalize_lines(lines)

        # idempotent replay：同 key 內容相同 → 回原單、不再退現；內容不同 → 拒絕。
        replay = await self.find_idempotent_replay(
            store_id,
            idempotency_key,
            sale_id=sale_id,
            requested=requested,
            reason=clean_reason,
        )
        if replay is not None:
            return replay

        sale = await self._sales.lock_sale(store_id, sale_id)
        if sale is None:
            raise ReturnSaleNotFound(f"找不到銷售單 {sale_id}")
        if sale.status == SaleStatus.RETURNED:
            raise ReturnConflict(f"銷售單 {sale_id} 已全數退貨，不可重複退貨")
        if sale.invoice_status == SaleInvoiceStatus.VOID:
            raise ReturnConflict(f"銷售單 {sale_id} 已作廢，不可退貨")

        sale_lines = await self._sales.list_lines(sale.id)
        lines_by_id = {line.id: line for line in sale_lines}
        previous = await self._repo.returned_qty_by_sale_line_ids(store_id, list(lines_by_id))

        refund_amount = Decimal(0)
        selected: list[tuple[SaleLine, int, Decimal]] = []
        for sale_line_id, qty in requested.items():
            line = lines_by_id.get(sale_line_id)
            if line is None:
                raise ReturnLineInvalid(f"銷售明細 {sale_line_id} 不屬於銷售單 {sale_id}")
            self._validate_supported_line(line)
            already_returned = previous.get(sale_line_id, 0)
            if already_returned + qty > line.qty:
                raise ReturnLineInvalid(
                    f"銷售明細 {sale_line_id} 可退數量不足（已退 {already_returned}）"
                )
            line_refund = line.unit_price * qty
            refund_amount += line_refund
            selected.append((line, qty, line_refund))

        self._ensure_cash_refund_supported(
            sale.payment_method, await self._sales.list_tenders(sale.id)
        )

        customer_return = await self._repo.add_return(
            CustomerReturn(
                store_id=store_id,
                sale_id=sale.id,
                refund_amount=refund_amount,
                reason=clean_reason,
                clerk_user_id=actor_user_id,
                idempotency_key=idempotency_key,
                idempotency_fingerprint=_return_fingerprint(sale.id, requested, clean_reason),
            )
        )

        for line, qty, line_refund in selected:
            await self._repo.add_line(
                ReturnLine(
                    store_id=store_id,
                    return_id=customer_return.id,
                    sale_line_id=line.id,
                    qty=qty,
                    refund_amount=line_refund,
                )
            )
            await self._return_inventory_line(store_id, customer_return.id, line, qty)
            # 退回寄售序號品 → 反轉其結算（invariant #7），即使只退這一品、整張單未全退。
            # 在現金出帳前先取得結算鎖，建立『結算 → cash_session』鎖序與 pay_settlement 一致，
            # 避免退貨↔付款死結（Codex High）。非寄售序號品無結算 → no-op。
            if line.line_type == SaleLineType.SERIALIZED:
                assert line.serialized_item_id is not None
                await self._consignment.cancel_settlement_for_sale_item(
                    store_id, sale.id, line.serialized_item_id, actor_user_id=actor_user_id
                )

        await self._cash.record_movement(
            store_id,
            CashMovementType.SALE_REFUND_OUT,
            refund_amount,
            actor_user_id=actor_user_id,
            ref_type="return",
            ref_id=customer_return.id,
        )

        returned_after = dict(previous)
        for sale_line_id, qty in requested.items():
            returned_after[sale_line_id] = returned_after.get(sale_line_id, 0) + qty
        if all(returned_after.get(line.id, 0) >= line.qty for line in sale_lines):
            sale.status = SaleStatus.RETURNED

        # 退貨按比例沖回會員點數（D-8(2)，裁示 2026-07-16）：
        # claw = floor(awarded_points × 本次退款 ÷ 原總額)。每次部分退貨各自按比例，
        # Σfloor ≤ awarded 不會超沖。點數可能已被會員用掉 → clamp 至現有餘額、
        # 不阻擋退貨（與作廢「整筆同生共死」不同：退款本身必須成立）。
        if sale.buyer_contact_id is not None and sale.awarded_points > 0 and sale.total > 0:
            claw = int(sale.awarded_points * refund_amount / sale.total)
            if claw > 0:
                from app.modules.contacts.service import ContactService

                contacts = ContactService(self._session)
                buyer = await contacts.get_contact_for_update(store_id, sale.buyer_contact_id)
                if buyer is not None:
                    clawed = min(claw, int(buyer.member_points))
                    if clawed > 0:
                        await contacts.add_member_points(
                            store_id, sale.buyer_contact_id, -clawed
                        )

        # 折讓（§7.5、不變量 5）：原銷售已「正式開票」（發票 ISSUED）→ 產 G0401 折讓單並標
        # sale.invoice_status=PENDING_ALLOWANCE；而非直接刪除發票。**比照 ISSUE/VOID：等 G0401
        # 平台 ProcessResult 成功後才由 einvoice 回呼轉正式 ALLOWANCE**（避免 G0401 上傳失敗卻已顯示
        # 已折讓）。折讓金額＝本次退款額；同退貨 return_id 唯一、累計不超過原發票（einvoice 守衛）。
        invoice = await self._einvoice.get_invoice_for_sale(store_id, sale.id)
        if invoice is not None and invoice.status == InvoiceStatus.ISSUED:
            # 稅拆分由 einvoice 以**原發票稅率快照**計（Codex 第十輪），不傳活 settings。
            await self._einvoice.record_allowance(
                store_id,
                invoice_id=invoice.id,
                total=refund_amount,
                return_id=customer_return.id,
            )
            sale.invoice_status = SaleInvoiceStatus.PENDING_ALLOWANCE
        elif (
            invoice is not None
            and invoice.status == InvoiceStatus.PENDING
            and sale.status == SaleStatus.RETURNED
        ):
            # 發票尚未平台核可期間即「全數退貨」：比照作廢收斂，不可放任 F0401 之後以全額核可
            # 卻無折讓（買了馬上退是門市真實場景）。void_invoice_for_sale 分流：F0401 未拋檔 →
            # 發票 VOID＋佇列 CANCELLED（平台從未收過）；已拋檔 → VOID_PENDING，由 F0401 回執
            # 決定（成功→續 F0501 作廢、失敗→VOID），最終由 einvoice 回呼收斂 sale 狀態。
            voided = await self._einvoice.void_invoice_for_sale(store_id, sale.id)
            if voided is not None and voided.status == InvoiceStatus.VOID:
                sale.invoice_status = SaleInvoiceStatus.NOT_ISSUED  # 未拋檔即取消：無有效發票
        # 部分退貨且發票仍 PENDING：不動——F0401 核可（發票成立）時由 einvoice 回呼
        # backfill_allowances_for_issued_sale 補開 G0401。

        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="CREATE_RETURN",
            entity_type="return",
            entity_id=str(customer_return.id),
            after={
                "sale_id": sale.id,
                "refund_amount": str(refund_amount),
                "line_count": len(selected),
            },
        )
        await self._session.flush()
        refreshed = await self._repo.get_return(store_id, customer_return.id)
        if refreshed is None:
            raise ReturnNotFound(f"找不到退貨單 {customer_return.id}")
        return refreshed

    async def backfill_allowances_for_issued_sale(self, store_id: int, sale_id: int) -> None:
        """發票（F0401）平台核可後，為「核可前已發生的退貨」補開 G0401 折讓（§7.5）。

        由 einvoice service 於 ISSUE 回執成功時回呼（跨模組經 service，§2）。退貨當下發票尚未
        成立（PENDING）無法開折讓；發票此刻成立 → 逐張退貨單補建折讓＋G0401 佇列，並把 sale
        轉 PENDING_ALLOWANCE（等 G0401 核可才轉正式 ALLOWANCE）。以 return_id 冪等（已有折讓
        者跳過）；無退貨 → no-op。全退場景不會走到這裡（退貨時已把發票導入作廢收斂）。
        """
        returns = await self._repo.list_returns_for_sale(store_id, sale_id)
        if not returns:
            return
        invoice = await self._einvoice.get_invoice_for_sale(store_id, sale_id)
        if invoice is None or invoice.status != InvoiceStatus.ISSUED:
            return
        created = False
        for customer_return in returns:
            existing = await self._einvoice.get_allowance_for_return(store_id, customer_return.id)
            if existing is not None:
                continue
            # 稅拆分由 einvoice 以**原發票稅率快照**計（Codex 第十輪），不傳活 settings。
            await self._einvoice.record_allowance(
                store_id,
                invoice_id=invoice.id,
                total=customer_return.refund_amount,
                return_id=customer_return.id,
            )
            created = True
        if created:
            sale = await self._sales.lock_sale(store_id, sale_id)
            if sale is not None and sale.invoice_status == SaleInvoiceStatus.ISSUED:
                sale.invoice_status = SaleInvoiceStatus.PENDING_ALLOWANCE
                await self._session.flush()

    @staticmethod
    def _normalize_lines(lines: Sequence[ReturnLineInput]) -> dict[int, int]:
        requested: dict[int, int] = {}
        for line in lines:
            if line.qty <= 0:
                raise ReturnLineInvalid("退貨數量必須 > 0")
            if line.sale_line_id in requested:
                raise ReturnLineInvalid(f"銷售明細 {line.sale_line_id} 重複列入退貨")
            requested[line.sale_line_id] = line.qty
        if not requested:
            raise ReturnLineInvalid("退貨單必須至少有一筆明細")
        return requested

    @staticmethod
    def _ensure_cash_refund_supported(
        payment_method: PaymentMethod, tenders: list[SaleTender]
    ) -> None:
        if not tenders and payment_method == PaymentMethod.CASH:
            return
        if not tenders:
            raise ReturnConflict("目前僅支援純現金銷售退貨")
        if any(t.tender_type != TenderType.CASH for t in tenders):
            raise ReturnConflict("目前僅支援純現金銷售退貨")

    @staticmethod
    def _validate_supported_line(line: SaleLine) -> None:
        if line.line_type == SaleLineType.CATALOG and line.catalog_product_id is not None:
            return
        if line.line_type == SaleLineType.SERIALIZED and line.serialized_item_id is not None:
            return
        if line.line_type == SaleLineType.BULK_LOT and line.bulk_lot_id is not None:
            return
        raise ReturnLineInvalid(f"銷售明細 {line.id} 品項參照不完整，無法退貨")

    async def _return_inventory_line(
        self, store_id: int, return_id: int, line: SaleLine, qty: int
    ) -> None:
        if line.line_type == SaleLineType.CATALOG:
            assert line.catalog_product_id is not None
            await self._inventory.return_catalog_items(
                store_id,
                line.catalog_product_id,
                qty,
                ref_type="return",
                ref_id=return_id,
            )
        elif line.line_type == SaleLineType.SERIALIZED:
            assert line.serialized_item_id is not None
            if qty != 1:
                raise ReturnLineInvalid(f"序號品銷售明細 {line.id} 退貨數量必須為 1")
            await self._inventory.return_serialized_sale_item(
                store_id,
                line.serialized_item_id,
                ref_type="return",
                ref_id=return_id,
            )
        else:
            assert line.bulk_lot_id is not None
            await self._inventory.return_bulk_lot_items(
                store_id,
                line.bulk_lot_id,
                qty,
                ref_type="return",
                ref_id=return_id,
            )
