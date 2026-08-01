"""returns 業務邏輯：建立退貨、退現、回補庫存、更新銷售狀態。"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.service import ConsignmentService
from app.modules.einvoice.service import EInvoiceService
from app.modules.inventory.service import InventoryService
from app.modules.returns.invoice_policy import (
    InvoiceFacts,
    ReturnInvoiceAction,
    ReturnInvoiceDecision,
    decide,
)
from app.modules.returns.models import CustomerReturn, ReturnLine, ReturnTender
from app.modules.returns.repository import ReturnsMarginAdjustments, ReturnsRepository
from app.modules.sales.linepay import LinePayClient
from app.modules.sales.models import SaleLine, SaleTender
from app.modules.sales.repository import SalesRepository
from app.modules.sales.service import SalesService
from app.modules.settings.service import StoreSettingsService
from app.modules.storecredit.service import StoreCreditService
from app.shared.enums import (
    CashMovementType,
    InvoiceStatus,
    InvoiceVoidReason,
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


def _refund_identity(
    sale_id: int, requested: dict[int, int], reason: str, previous: dict[int, int]
) -> str:
    """LINE Pay 退款的伺服器端穩定身分（Codex 第四輪 #1）：sale + 本次退貨行/原因 + **退貨前累計
    已退量**。同一筆退貨的任何重試恆得同值（前端鍵無關）→ durable 日誌認得、不重退；分批退同行別因
    退貨前累計已退量遞增而得不同值 → 各自退。截 32 字元供 refund_key。"""
    canonical = {
        "sale_id": sale_id,
        "reason": reason,
        "lines": sorted(
            ({"sale_line_id": k, "qty": v} for k, v in requested.items()),
            key=lambda d: d["sale_line_id"],
        ),
        "prior_returned": sorted(
            ({"sale_line_id": k, "qty": v} for k, v in previous.items()),
            key=lambda d: d["sale_line_id"],
        ),
    }
    payload = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


_TAIPEI_TZ = ZoneInfo("Asia/Taipei")


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

    async def preview_return(
        self,
        store_id: int,
        *,
        sale_id: int,
        lines: Sequence[ReturnLineInput],
    ) -> dict[str, object]:
        """唯讀預覽：本次退貨會如何處置原發票。不寫入任何資料。"""
        sale = await self._sales.get_sale(store_id, sale_id)
        if sale is None:
            raise ReturnSaleNotFound(f"找不到銷售單 {sale_id}")
        requested = self._normalize_lines(lines)
        sale_lines = await self._sales.list_lines(sale_id)
        lines_by_id = {line.id: line for line in sale_lines}
        for sale_line_id in requested:
            if sale_line_id not in lines_by_id:
                raise ReturnLineInvalid(f"銷售明細 {sale_line_id} 不屬於銷售單 {sale_id}")
        previous = await self._repo.returned_qty_by_sale_line_ids(store_id, list(lines_by_id))
        after = dict(previous)
        for sale_line_id, qty in requested.items():
            after[sale_line_id] = after.get(sale_line_id, 0) + qty
        is_full_return = all(after.get(line.id, 0) >= line.qty for line in sale_lines)
        decision = await self._decide_invoice_action(
            store_id, sale_id, is_full_return=is_full_return
        )
        return {
            "is_full_return": is_full_return,
            "invoice_action": decision.action.value,
            "requires_paper_recall": decision.requires_paper_recall,
            "requires_customer_consent": decision.requires_customer_consent,
            "reason": decision.reason,
        }

    async def _decide_invoice_action(
        self, store_id: int, sale_id: int, *, is_full_return: bool
    ) -> ReturnInvoiceDecision:
        """自 DB 蒐集原發票事實，交由純政策模組決定折讓／作廢／轉人工（見 invoice_policy）。"""
        invoice = await self._einvoice.get_invoice_for_sale(store_id, sale_id)
        if invoice is None:
            return decide(
                InvoiceFacts(
                    exists=False,
                    is_issued=False,
                    issued_at=None,
                    has_settled_allowance=False,
                    has_open_allowance=False,
                    has_inflight_void=False,
                    print_mark=False,
                    carrier_type=None,
                    donate_mark=False,
                ),
                is_full_return=is_full_return,
                now=datetime.now(UTC),
            )
        # 開立日以平台回填的 invoice_date（台北曆日）為準；尚未回填時退回建立時間。
        issued_at = (
            datetime.combine(invoice.invoice_date, time(12, 0), tzinfo=_TAIPEI_TZ)
            if invoice.invoice_date is not None
            else invoice.created_at
        )
        facts = InvoiceFacts(
            exists=True,
            is_issued=invoice.status is InvoiceStatus.ISSUED,
            issued_at=issued_at,
            has_settled_allowance=await self._einvoice.has_settled_allowance(store_id, invoice.id),
            has_open_allowance=await self._einvoice.has_open_allowance(store_id, invoice.id),
            has_inflight_void=await self._einvoice.has_inflight_void(store_id, invoice.id),
            print_mark=invoice.print_mark,
            carrier_type=invoice.carrier_type,
            donate_mark=invoice.donate_mark,
        )
        return decide(facts, is_full_return=is_full_return, now=datetime.now(UTC))

    async def _require_return_consent(
        self,
        store_id: int,
        *,
        sale_id: int,
        signature_task_id: int | None,
        action: ReturnInvoiceAction,
        return_lines: dict[int, int],
        invoice_id: int | None,
        refund_total: Decimal,
    ) -> None:
        """買受人同意（電子發票實施作業要點第 9 點）：折讓與作廢皆須客人簽名確認。

        fail-closed：未帶已簽任務即拒絕——不可因「畫面沒顯示」就默默略過同意證據。
        同意的**範圍**也要對得上：客人簽的品項/數量必須正是本次要退的（見 consume_return_consent）。
        """
        if signature_task_id is None:
            raise ReturnConflict(
                "本次退貨會變更電子發票（"
                + ("作廢" if action is ReturnInvoiceAction.VOID else "開立折讓")
                + "），依規定須經買受人同意：請先請客人於顧客螢幕簽名確認。"
            )
        from app.modules.signing.service import SigningService

        await SigningService(self._session).consume_return_consent(
            store_id,
            signature_task_id,
            sale_id=sale_id,
            return_lines=return_lines,
            invoice_id=invoice_id,
            invoice_action=action.value,
            refund_total=refund_total,
        )

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
        linepay_client: LinePayClient | None = None,
        taiwan_pay_refund_confirmed: bool = False,
        invoice_recalled: bool = False,
        consent_signature_task_id: int | None = None,
    ) -> CustomerReturn:
        """建立退貨單並執行副作用；成功前只 flush，不 commit。

        支援現金、購物金、LINE Pay、台灣Pay及「購物金＋單一外部渠道」銷售的
        catalog / serialized / bulk 退貨。行數量驗證以既有 return_lines 聚合防重複退；
        sale 列以 FOR UPDATE 鎖住，避免並行重退同一張單。

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
        if sale.status is SaleStatus.VOIDED:
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

        # ── 發票處置政策（必須在任何退款動作之前）────────────────────────────────
        # LINE Pay 退款是**外部 API**，一旦呼叫就不會因交易回滾而收回；因此凡是可能「拒絕本次
        # 退貨」的判斷，都必須在此先做完，不得等到退款之後才擋。
        returned_after_preview = dict(previous)
        for sale_line_id, qty in requested.items():
            returned_after_preview[sale_line_id] = returned_after_preview.get(sale_line_id, 0) + qty
        will_be_full_return = all(
            returned_after_preview.get(line.id, 0) >= line.qty for line in sale_lines
        )
        invoice_decision = await self._decide_invoice_action(
            store_id, sale.id, is_full_return=will_be_full_return
        )
        decided_invoice = await self._einvoice.get_invoice_for_sale(store_id, sale.id)
        decided_invoice_id = decided_invoice.id if decided_invoice is not None else None
        if invoice_decision.action is ReturnInvoiceAction.REVIEW_REQUIRED:
            raise ReturnConflict(invoice_decision.reason)
        if invoice_decision.requires_paper_recall and not invoice_recalled:
            # 店主裁示（2026-08-01）：累計全退且原發票有紙本時，未收回紙本一律拒絕退貨退款。
            # 真正的部分退貨不受此限——原發票對未退商品仍是客人的憑證，不得收回。
            raise ReturnConflict(
                "本次為整筆退貨且原發票有紙本證明聯：請先向客人收回發票並於畫面確認，才能退貨退款。"
            )
        if invoice_decision.requires_customer_consent:
            await self._require_return_consent(
                store_id,
                sale_id=sale.id,
                signature_task_id=consent_signature_task_id,
                action=invoice_decision.action,
                return_lines=requested,
                invoice_id=decided_invoice_id,
                refund_total=refund_amount,
            )

        sale_tenders = await self._sales.list_tenders(sale.id)
        previous_refund = sum(
            (line.unit_price * previous.get(line.id, 0) for line in sale_lines), Decimal(0)
        )
        refund_allocations = self._refund_allocations(
            sale.payment_method,
            sale_tenders,
            previous_refund=previous_refund,
            refund_amount=refund_amount,
        )
        if any(kind == TenderType.TAIWAN_PAY for kind, _ in refund_allocations):
            if not taiwan_pay_refund_confirmed:
                raise ReturnConflict("請先在台灣Pay完成退款，並確認本次退款金額")

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

        for tender_type, amount in refund_allocations:
            await self._repo.add_tender(
                ReturnTender(
                    store_id=store_id,
                    return_id=customer_return.id,
                    tender_type=tender_type,
                    amount=amount,
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

        # 退款反轉（docs/30 §5）：純現金→錢櫃 SALE_REFUND_OUT（需開帳）；純 LINE Pay→呼叫 refund
        # API 部分退款（累加 refunded_amount、非現金不進抽屜、不需開帳）。fail-closed：LINE Pay 退款
        # 失敗整筆退貨回滾（不留已退貨卻未退款）。
        cash_refund = next(
            (amount for kind, amount in refund_allocations if kind == TenderType.CASH), Decimal(0)
        )
        if cash_refund > 0:
            # 固定 cash_session→store_credit_account 鎖序（與結帳／收購作廢一致），避免
            # 購物金＋現金退款和同會員的其他混合金流形成 AB-BA；退款分配仍維持購物金優先。
            await self._cash.record_movement(
                store_id,
                CashMovementType.SALE_REFUND_OUT,
                cash_refund,
                actor_user_id=actor_user_id,
                ref_type="return",
                ref_id=customer_return.id,
            )

        store_credit_refund = next(
            (amount for kind, amount in refund_allocations if kind == TenderType.STORE_CREDIT),
            Decimal(0),
        )
        if store_credit_refund > 0:
            if sale.buyer_contact_id is None:
                raise ReturnConflict("購物金退貨缺少原會員，無法回補")
            await StoreCreditService(self._session).refund_for_sale_return(
                store_id,
                sale.buyer_contact_id,
                amount=store_credit_refund,
                return_id=customer_return.id,
                created_by=actor_user_id,
            )

        linepay_refund = next(
            (amount for kind, amount in refund_allocations if kind == TenderType.LINE_PAY),
            Decimal(0),
        )
        if linepay_refund > 0:
            # refund_key **由伺服器端內容導出**（Codex 第四輪 #1），非用前端冪等鍵：綁 (店, 銷售,
            # 本次退貨行/原因, 退貨前累計已退量)。同一筆退貨的任何重試（即使換前端鍵——localStorage
            # 遺失/換收銀機/PENDING 被人工標 SUCCEEDED 後重做）恆得同 refund_key → durable 日誌認得
            # 已退、不重退；兩筆行別相同的合法分批退貨，退貨前累計已退量不同 → 不同 key → 各自退。
            refund_identity = _refund_identity(sale.id, requested, clean_reason, previous)
            await SalesService(self._session).refund_line_pay_amount(
                store_id,
                sale.id,
                linepay_refund,
                linepay_client,
                refund_key=f"s{store_id}:return:{refund_identity}",
            )

        returned_after = dict(previous)
        for sale_line_id, qty in requested.items():
            returned_after[sale_line_id] = returned_after.get(sale_line_id, 0) + qty
        # 「累計全退」＝本次退完後所有明細都退光。含餐飲的混合單因餐飲不可退，永遠不成立
        # ——這正確：餐飲確實沒退，本來就不算整筆退。
        is_full_return = all(returned_after.get(line.id, 0) >= line.qty for line in sale_lines)
        if is_full_return:
            sale.status = SaleStatus.RETURNED

        # 退貨按比例沖回會員點數（D-8(2)，裁示 2026-07-16；Codex 波次二第三輪 P1 修正口徑）：
        # 點數當初只發**非餐飲**部分 floor((total−餐飲)/100)，故沖點須以「非餐飲」為分母，
        # 且用**差額法累積**（非逐次獨立 floor，否則 $150/3 件分退會殘留 1 點；同散裝 COGS）：
        #   entitlement(x) = floor(awarded × 累積非餐飲退款 x ÷ 原非餐飲小計)
        #   本次沖點 = entitlement(含本次) − entitlement(本次前)
        # 全退時 entitlement=awarded、逐次差額加總=awarded，餐飲混單也不會少沖。
        # 點數可能已被會員用掉 → clamp 至現有餘額、不阻擋退貨（退款本身必須成立）。
        non_menu_subtotal = sum(
            (line.line_total for line in sale_lines if line.line_type != SaleLineType.MENU),
            Decimal(0),
        )
        if sale.buyer_contact_id is not None and sale.awarded_points > 0 and non_menu_subtotal > 0:
            prior_refund = sum(
                (
                    previous.get(line.id, 0) * line.unit_price
                    for line in sale_lines
                    if line.line_type != SaleLineType.MENU
                ),
                Decimal(0),
            )
            # 本次退貨全為非餐飲（餐飲不可退，_validate_supported_line 已擋）→ refund_amount
            awarded = Decimal(sale.awarded_points)
            prior_ent = int(awarded * prior_refund / non_menu_subtotal)
            now_ent = int(awarded * (prior_refund + refund_amount) / non_menu_subtotal)
            claw = now_ent - prior_ent
            if claw > 0:
                from app.modules.contacts.service import ContactService

                contacts = ContactService(self._session)
                buyer = await contacts.get_contact_for_update(store_id, sale.buyer_contact_id)
                if buyer is not None:
                    clawed = min(claw, int(buyer.member_points))
                    if clawed > 0:
                        await contacts.add_member_points(store_id, sale.buyer_contact_id, -clawed)

        # 折讓（§7.5、不變量 5）：原銷售已「正式開票」（發票 ISSUED）→ 產 G0401 折讓單並標
        # sale.invoice_status=PENDING_ALLOWANCE；而非直接刪除發票。**比照 ISSUE/VOID：等 G0401
        # 平台 ProcessResult 成功後才由 einvoice 回呼轉正式 ALLOWANCE**（避免 G0401 上傳失敗卻已顯示
        # 已折讓）。折讓金額＝本次退款額；同退貨 return_id 唯一、累計不超過原發票（einvoice 守衛）。
        invoice = await self._einvoice.get_invoice_for_sale(store_id, sale.id)
        if (
            invoice is not None
            and invoice.status == InvoiceStatus.ISSUED
            and invoice_decision.action is ReturnInvoiceAction.VOID
        ):
            # 整筆退貨且原發票為本月開立 → **作廢原發票（F0501）**，不開折讓（ADR-014）。
            # 紙本收回與買受人同意已於本函式前段驗證（在任何退款動作之前）。
            await self._einvoice.void_invoice_for_sale(
                store_id, sale.id, reason=InvoiceVoidReason.FULL_RETURN
            )
            # 銷售本身有效（status=RETURNED），只是那張發票作廢了。
            sale.invoice_status = SaleInvoiceStatus.VOID
        elif invoice is not None and invoice.status == InvoiceStatus.ISSUED:
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
                "refund_tenders": [
                    {"tender_type": kind.value, "amount": str(amount)}
                    for kind, amount in refund_allocations
                ],
                "line_count": len(selected),
                "invoice_action": invoice_decision.action.value,
                "invoice_recalled": invoice_recalled,
                "consent_signature_task_id": consent_signature_task_id,
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
    def _refund_allocations(
        payment_method: PaymentMethod,
        tenders: list[SaleTender],
        *,
        previous_refund: Decimal,
        refund_amount: Decimal,
    ) -> list[tuple[TenderType, Decimal]]:
        """按累計退款做差額拆帳：購物金優先，其餘僅支援單一付款。"""
        if not tenders:
            if payment_method == PaymentMethod.CASH:
                return [(TenderType.CASH, refund_amount)]
            raise ReturnConflict("原銷售缺少付款明細，無法判定退款去向")

        amounts = {t.tender_type: Decimal(t.amount) for t in tenders}
        supported_external = {TenderType.CASH, TenderType.LINE_PAY, TenderType.TAIWAN_PAY}
        kinds = set(amounts)
        external = kinds - {TenderType.STORE_CREDIT}
        if any(kind not in supported_external for kind in external):
            raise ReturnConflict("原銷售含不支援的退款渠道")

        if TenderType.STORE_CREDIT in kinds:
            if len(external) > 1:
                raise ReturnConflict("購物金退款僅支援搭配單一現金／LINE Pay／台灣Pay付款")
            priority = [TenderType.STORE_CREDIT, *external]
        elif len(kinds) == 1 and kinds <= supported_external:
            priority = list(kinds)
        else:
            raise ReturnConflict("退款僅支援單一付款或購物金搭配一種其他付款")

        total_paid = sum(amounts.values(), Decimal(0))
        if (
            previous_refund < 0
            or refund_amount <= 0
            or previous_refund + refund_amount > total_paid
        ):
            raise ReturnConflict("累計退款金額超過原付款渠道金額")

        allocations: list[tuple[TenderType, Decimal]] = []
        priority_capacity = Decimal(0)
        for tender_type in priority:
            capacity = amounts[tender_type]
            refunded_before = min(capacity, max(Decimal(0), previous_refund - priority_capacity))
            refunded_after = min(
                capacity,
                max(Decimal(0), previous_refund + refund_amount - priority_capacity),
            )
            delta = refunded_after - refunded_before
            if delta > 0:
                allocations.append((tender_type, delta))
            priority_capacity += capacity
        return allocations

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
