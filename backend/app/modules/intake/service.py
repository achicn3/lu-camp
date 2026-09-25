"""收購佇列 service（docs/42）：報到收件、估價、叫號確認、取消。業務規則集中於此。

計價分工沿用收購頁（docs/42 §11）：建議收購價與預計售價由前端同一套 `pricing.ts` 算好送來，
後端只驗金額合法與欄位一致，不重算建議價——成交價本來就可由店員改（裁示 5）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import format_ntd
from app.core.time import store_date, utc_now
from app.modules.acquisition.schemas import AcquisitionCreate, AcquisitionItemIn, AcquisitionLotIn
from app.modules.acquisition.service import AcquisitionService
from app.modules.contacts.service import ContactService
from app.modules.intake.models import IntakeBatch, IntakeBatchAcquisition, IntakeLine
from app.modules.intake.repository import IntakeRepository
from app.modules.intake.schemas import (
    IntakeBatchRead,
    IntakeDispositionRequest,
    IntakeLineFields,
    IntakeLineRead,
    IntakeReceiptItem,
    IntakeReceiptRead,
)
from app.modules.inventory.service import InventoryService
from app.modules.signing.models import SignatureTask
from app.modules.signing.schemas import SignatureTaskCreate
from app.modules.signing.service import SigningService
from app.modules.storecredit.service import StoreCreditService
from app.shared.enums import (
    AcquisitionType,
    BulkAcquisitionBasis,
    IntakeBatchStatus,
    IntakeDisposition,
    PayoutMethod,
    SignatureTaskKind,
    SignatureTaskStatus,
    StoreCreditEntryType,
    StoreCreditSourceType,
)
from app.shared.exceptions import IntakeBatchNotFound, IntakeConflict, InvalidIntakeLine

# 撞號重取的上限（同叫號系統）：單店單機並發極低，超過就是異常。
_ALLOCATION_RETRIES = 3
_UNIQUE_VIOLATION_SQLSTATE = "23505"
TICKET_PREFIX = "A"  # 收購用 A 字首，與餐飲叫號分開（裁示 2）
SLIP_CODE_PREFIX = "IN"  # 收件單條碼字首

# 還沒簽署以前，估價列都能改（叫號議價時店員可改成交價，裁示 5）。
_EDITABLE = frozenset(
    {
        IntakeBatchStatus.PENDING_ESTIMATE,
        IntakeBatchStatus.ESTIMATING,
        IntakeBatchStatus.AWAITING_CONFIRM,
    }
)
# 進入「待確認」後就不刪列（改用處置記錄），之後才查得到當時收了什麼、退了什麼。
_DELETABLE = frozenset({IntakeBatchStatus.PENDING_ESTIMATE, IntakeBatchStatus.ESTIMATING})
# 估價列的必填欄位：修改時帶 null 不能清掉（其他欄位帶 null＝清掉那個選填值）。
_REQUIRED_LINE_FIELDS = ("short_name", "qty", "acquisition_type")
OPEN_STATUSES = [
    IntakeBatchStatus.PENDING_ESTIMATE,
    IntakeBatchStatus.ESTIMATING,
    IntakeBatchStatus.AWAITING_CONFIRM,
    IntakeBatchStatus.SIGNED,
]


def ticket_label(ticket_no: int) -> str:
    return f"{TICKET_PREFIX}{ticket_no:03d}"


def slip_code(batch_id: int) -> str:
    """收件單條碼：批次 id 永久唯一（當日號碼每天重編，不能拿來當條碼）。Code39 可編的字元。"""
    return f"{SLIP_CODE_PREFIX}{batch_id:06d}"


def _is_unique_violation(exc: IntegrityError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == _UNIQUE_VIOLATION_SQLSTATE


_PAID_STATUSES = frozenset(
    {IntakeBatchStatus.PAID, IntakeBatchStatus.PARTIALLY_LISTED, IntakeBatchStatus.LISTED}
)

# 送簽後被撤回、逾時或失敗：這份簽名作廢，付款當作沒簽（本店要求簽署時由收購流程擋下）。
_SIGNATURE_GONE = frozenset(
    {SignatureTaskStatus.VOIDED, SignatureTaskStatus.EXPIRED, SignatureTaskStatus.FAILED}
)


class IntakeService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = IntakeRepository(session)
        self._contacts = ContactService(session)
        self._inventory = InventoryService(session)

    # ── 報到收件 ──────────────────────────────────────────────────────

    async def create_batch(
        self,
        store_id: int,
        *,
        contact_id: int,
        declared_item_count: int,
        actor_user_id: int,
        note: str | None = None,
        now: datetime | None = None,
    ) -> IntakeBatch:
        """建立批次並配當日號碼（同店同一台北營業日從 1 起）。`now` 僅供測試注入時點。"""
        if await self._contacts.get_contact(store_id, contact_id) is None:
            raise IntakeBatchNotFound(f"找不到這位賣方（id={contact_id}）")
        ticket_date = store_date(now if now is not None else utc_now())
        for attempt in range(_ALLOCATION_RETRIES):
            batch = IntakeBatch(
                store_id=store_id,
                contact_id=contact_id,
                ticket_date=ticket_date,
                ticket_no=await self._repo.next_ticket_no(store_id, ticket_date),
                declared_item_count=declared_item_count,
                status=IntakeBatchStatus.PENDING_ESTIMATE,
                note=(note or "").strip() or None,
                created_by_user_id=actor_user_id,
            )
            try:
                # savepoint：撞號只退這一筆新增，不回滾呼叫端的其他工作。
                async with self._session.begin_nested():
                    self._repo.add(batch)
                    await self._session.flush()
            except IntegrityError as exc:
                if not _is_unique_violation(exc) or attempt == _ALLOCATION_RETRIES - 1:
                    raise
                continue
            return batch
        raise AssertionError("unreachable")  # pragma: no cover

    # ── 估價 ──────────────────────────────────────────────────────────

    async def add_line(self, store_id: int, batch_id: int, fields: IntakeLineFields) -> IntakeLine:
        """新增一列估價（隨時存檔）。第一列存下去，批次就從「待估價」進「估價中」。"""
        batch = await self._editable_batch(store_id, batch_id)
        line = IntakeLine(
            store_id=store_id,
            batch_id=batch.id,
            line_no=await self._repo.next_line_no(batch.id),
            **fields.model_dump(),
        )
        await self._check_line(store_id, line)
        self._repo.add(line)
        if batch.status is IntakeBatchStatus.PENDING_ESTIMATE:
            batch.status = IntakeBatchStatus.ESTIMATING
        await self._session.flush()
        return line

    async def update_line(
        self, store_id: int, batch_id: int, line_id: int, fields: IntakeLineFields
    ) -> IntakeLine:
        """修改估價列：只改有帶的欄位（帶 null＝清掉）。簽署以前都能改。"""
        await self._editable_batch(store_id, batch_id)
        line = await self._line(store_id, batch_id, line_id)
        changes = fields.model_dump(exclude_unset=True)
        cleared = [
            name for name in _REQUIRED_LINE_FIELDS if name in changes and changes[name] is None
        ]
        if cleared:
            raise InvalidIntakeLine("商品簡稱、數量、類型不能清空")
        for name, value in changes.items():
            setattr(line, name, value)
        await self._check_line(store_id, line)
        if line.accepted_qty > line.qty:
            raise InvalidIntakeLine("數量不能少於已接受的件數，請先改處置")
        await self._session.flush()
        return line

    async def delete_line(self, store_id: int, batch_id: int, line_id: int) -> None:
        """估價中打錯的列可以刪；進入待確認後不刪，改用處置記錄。"""
        batch = await self._batch(store_id, batch_id, for_update=True)
        if batch.status not in _DELETABLE:
            raise IntakeConflict("這一批已經估完，列不能刪；請改用處置（客人不售／店家不收）")
        await self._repo.delete_line(await self._line(store_id, batch_id, line_id))

    async def mark_ready(self, store_id: int, batch_id: int) -> IntakeBatch:
        """估完 → 待確認（等叫號議價）。每一列都要能報價：買斷／散裝有成交價、寄售有抽成。"""
        batch = await self._batch(store_id, batch_id, for_update=True)
        if batch.status is IntakeBatchStatus.AWAITING_CONFIRM:
            return batch
        if batch.status is not IntakeBatchStatus.ESTIMATING:
            raise IntakeConflict("這一批還沒有估價列，或已經不在估價階段")
        lines = await self._repo.lines_for(store_id, [batch.id])
        missing = [
            line.line_no
            for line in lines
            if (
                line.acquisition_type is AcquisitionType.CONSIGNMENT and line.commission_pct is None
            )
            or (line.acquisition_type is not AcquisitionType.CONSIGNMENT and line.deal_cost is None)
        ]
        if missing:
            numbers = "、".join(str(n) for n in missing)
            raise IntakeConflict(f"第 {numbers} 列還沒有收購價（寄售要填抽成），不能送去叫號")
        # 付款當下就建庫存（店主 2026-09-25）：商品要有售價，序號品要有成色。
        no_price = [line.line_no for line in lines if line.expected_listed_price is None]
        if no_price:
            numbers = "、".join(str(n) for n in no_price)
            raise IntakeConflict(f"第 {numbers} 列還沒有預計售價，不能送去叫號")
        no_grade = [
            line.line_no
            for line in lines
            if line.acquisition_type is not AcquisitionType.BULK_LOT and line.grade is None
        ]
        if no_grade:
            numbers = "、".join(str(n) for n in no_grade)
            raise IntakeConflict(f"第 {numbers} 列還沒有成色，不能送去叫號")
        batch.status = IntakeBatchStatus.AWAITING_CONFIRM
        await self._session.flush()
        return batch

    # ── 叫號確認 ──────────────────────────────────────────────────────

    async def set_disposition(
        self, store_id: int, batch_id: int, line_id: int, request: IntakeDispositionRequest
    ) -> IntakeLine:
        """逐列處置（可部分接受）；沒成交的件是否已交還客人一起記。

        取消的批次只能記退還（沒有成交可言）。
        """
        batch = await self._batch(store_id, batch_id, for_update=True)
        line = await self._line(store_id, batch_id, line_id)
        if batch.status is IntakeBatchStatus.CANCELLED:
            if request.disposition is IntakeDisposition.ACCEPTED or request.accepted_qty:
                raise IntakeConflict("這一批已取消，只能記錄是否已交還客人")
        elif batch.status is not IntakeBatchStatus.AWAITING_CONFIRM:
            raise IntakeConflict("這一批還沒估完或已經簽署，不能改處置")
        accepted = self._accepted_qty(line, request)
        line.disposition = request.disposition
        line.accepted_qty = accepted
        # 全部成交就沒有東西要還，旗標一律清掉，免得清單上出現「已退還 0 件」。
        line.returned_to_customer = request.returned_to_customer and accepted < line.qty
        await self._session.flush()
        return line

    async def cancel(
        self, store_id: int, batch_id: int, *, reason: str, actor_user_id: int
    ) -> IntakeBatch:
        """客人放棄整批（簽署以前）。列不刪；東西有沒有領回，逐列用處置記。"""
        batch = await self._batch(store_id, batch_id, for_update=True)
        if batch.status is IntakeBatchStatus.CANCELLED:
            return batch
        if batch.status not in _EDITABLE:
            raise IntakeConflict("這一批已經簽署或付款，不能取消；付款後請走作廢收購")
        batch.status = IntakeBatchStatus.CANCELLED
        batch.cancelled_at = utc_now()
        batch.cancelled_by_user_id = actor_user_id
        batch.cancel_reason = reason.strip()
        await self._session.flush()
        return batch

    # ── 簽署與付款（I3）────────────────────────────────────────────────

    async def request_signature(
        self, store_id: int, batch_id: int, *, terminal_id: int | None, actor_user_id: int
    ) -> SignatureTask:
        """把整批要付錢的商品送到顧客螢幕給客人簽（一批一份切結；內容由後端依接受的列產生）。

        簽完再改品項或金額 → 付款時比對不符、要重簽（docs/42 §6）。
        """
        batch = await self._batch(store_id, batch_id, for_update=True)
        if batch.status is not IntakeBatchStatus.AWAITING_CONFIRM:
            raise IntakeConflict("要先估完、叫號確認後才能送簽署")
        lines = await self._repo.lines_for(store_id, [batch.id])
        self._ensure_decided(lines)
        content = self._affidavit_content(lines)
        if content is None:
            raise IntakeConflict("這一批沒有要付錢的商品（寄售不付現），不需要簽署")
        task = await SigningService(self._session).create_task(
            store_id,
            SignatureTaskCreate(
                kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
                contact_id=batch.contact_id,
                content=content,
                terminal_id=terminal_id,
                ref_type="intake_batch",
                ref_id=batch.id,
            ),
            created_by=actor_user_id,
        )
        batch.signature_task_id = task.id
        await self._session.flush()
        return task

    async def pay(
        self, store_id: int, batch_id: int, *, payout_method: PayoutMethod, actor_user_id: int
    ) -> IntakeBatch:
        """付款：依類型成立收購（沿用現有收購的撥款、錢櫃、購物金），商品建成「待整理」。

        **冪等**：已付款再按回原結果，不再付一次錢。整個動作在同一個交易：任何一筆收購失敗
        （沒開帳、購物金資格…），全部回滾、批次維持待確認。
        """
        batch = await self._batch(store_id, batch_id, for_update=True)
        if batch.status is IntakeBatchStatus.PAID:
            return batch
        if batch.status is not IntakeBatchStatus.AWAITING_CONFIRM:
            raise IntakeConflict("要先估完、叫號確認後才能付款")
        lines = await self._repo.lines_for(store_id, [batch.id])
        self._ensure_decided(lines)
        accepted = [line for line in lines if self._accepted(line)]
        if not accepted:
            raise IntakeConflict("沒有接受的商品，不能付款；客人都不賣請按「取消整批」")

        affidavit: SignatureTask | None = None
        content = self._affidavit_content(lines)
        if content is not None and batch.signature_task_id is not None:
            affidavit = await self._signed_affidavit(store_id, batch, content)
        if affidavit is not None:
            assert affidavit.chosen_payout is not None
            payout_method = affidavit.chosen_payout  # 以客人在顧客螢幕選的為準

        acquisitions = AcquisitionService(self._session)
        for n, data in enumerate(self._acquisition_requests(batch, accepted, payout_method)):
            result = await acquisitions.create_acquisition(
                store_id,
                actor_user_id,
                data,
                idempotency_key=f"intake-{batch.id}-{n}",
                pending_listing=True,
                batch_affidavit=affidavit if data.type is not AcquisitionType.CONSIGNMENT else None,
            )
            self._repo.add(
                IntakeBatchAcquisition(
                    store_id=store_id, batch_id=batch.id, acquisition_id=result.acquisition_id
                )
            )
        if affidavit is not None:
            await SigningService(self._session).consume_task(
                affidavit, reason_code="INTAKE_BATCH_PAID", actor_user_id=actor_user_id
            )
        batch.status = IntakeBatchStatus.PAID
        batch.paid_at = utc_now()
        batch.paid_by_user_id = actor_user_id
        await self._session.flush()
        return batch

    async def _signed_affidavit(
        self, store_id: int, batch: IntakeBatch, content: dict[str, object]
    ) -> SignatureTask | None:
        """最近一次送簽：已簽 → 驗證後回傳；客人還在簽 → 擋；撤回／逾時 → 當作沒簽（None）。"""
        assert batch.signature_task_id is not None
        task = await SigningService(self._session).get_task(store_id, batch.signature_task_id)
        if task is None or task.status in _SIGNATURE_GONE:
            return None
        if task.status is not SignatureTaskStatus.SIGNED:
            raise IntakeConflict("客人還在顧客螢幕上簽名，簽完再付款；要改內容請先撤回簽名")
        return await self._verified_affidavit(store_id, batch, content)

    async def _verified_affidavit(
        self, store_id: int, batch: IntakeBatch, content: dict[str, object]
    ) -> SignatureTask:
        """已簽切結必須就是現在要付的這批：品項與金額精確相符、身分沒換（同收購頁規則）。"""
        assert batch.signature_task_id is not None
        task = await SigningService(self._session).get_signed_affidavit(
            store_id, batch.signature_task_id, contact_id=batch.contact_id
        )
        signed = {"items": task.content.get("items"), "total": task.content.get("total")}
        if signed != content:
            raise IntakeConflict("商品或金額在簽署後改過，請重新簽署")
        contact = await self._contacts.get_contact_for_update(store_id, batch.contact_id)
        if (
            contact is None
            or not task.identity_fingerprint
            or task.identity_fingerprint != contact.national_id_blind_index
        ):
            raise IntakeConflict("賣方身分與簽署時不同，請重新簽署")
        if task.chosen_payout is None:
            raise IntakeConflict("簽署缺少客人選的撥款方式，請重新簽署")
        return task

    @staticmethod
    def _ensure_decided(lines: list[IntakeLine]) -> None:
        """每一列都要先選好處置才能簽署／付款：付完款還懸著的列，商品會不知道歸誰。"""
        undecided = [
            line.line_no for line in lines if line.disposition is IntakeDisposition.PENDING
        ]
        if undecided:
            numbers = "、".join(str(n) for n in undecided)
            raise IntakeConflict(f"第 {numbers} 列還沒選「接受／客人不售／店家不收」")

    @staticmethod
    def _accepted(line: IntakeLine) -> bool:
        return line.disposition is IntakeDisposition.ACCEPTED and line.accepted_qty > 0

    @classmethod
    def _affidavit_content(cls, lines: list[IntakeLine]) -> dict[str, object] | None:
        """切結內容（客人簽的就是要付錢的東西）：買斷逐件、散裝一列一筆（名稱帶件數）。

        格式同收購頁切結（items＝[{name, amount}]、total），簽署服務會再整理成標準形狀。
        寄售不付現、不進切結；整批都沒有要付錢的回 None。
        """
        items: list[dict[str, str]] = []
        total = Decimal(0)
        for line in lines:
            if not cls._accepted(line) or line.deal_cost is None:
                continue
            if line.acquisition_type is AcquisitionType.BUYOUT:
                for _ in range(line.accepted_qty):
                    items.append({"name": line.short_name, "amount": format_ntd(line.deal_cost)})
                total += Decimal(line.deal_cost) * line.accepted_qty
            elif line.acquisition_type is AcquisitionType.BULK_LOT:
                amount = Decimal(line.deal_cost) * line.accepted_qty
                items.append(
                    {
                        "name": f"{line.short_name} ×{line.accepted_qty}",
                        "amount": format_ntd(amount),
                    }
                )
                total += amount
        if not items:
            return None
        return {"items": items, "total": format_ntd(total)}

    @staticmethod
    def _acquisition_requests(
        batch: IntakeBatch, accepted: list[IntakeLine], payout_method: PayoutMethod
    ) -> list[AcquisitionCreate]:
        """依類型組收購：買斷一筆（逐件）、寄售一筆（逐件）、散裝一列一筆。"""
        note = f"排隊收購 A{batch.ticket_no:03d}（{batch.ticket_date.isoformat()}）"

        def item(line: IntakeLine, consignment: bool) -> AcquisitionItemIn:
            assert line.grade is not None and line.expected_listed_price is not None
            return AcquisitionItemIn(
                name=line.short_name,
                grade=line.grade,
                listed_price=line.expected_listed_price,
                brand_id=line.brand_id,
                product_model_id=line.product_model_id,
                category_id=line.category_id,
                acquisition_cost=None if consignment else line.deal_cost,
                retail_price=line.reference_price,
                resale_discount_pct=line.discount_pct,
                commission_pct=line.commission_pct if consignment else None,
                note=line.note,
            )

        requests: list[AcquisitionCreate] = []
        buyout = [
            item(line, False)
            for line in accepted
            if line.acquisition_type is AcquisitionType.BUYOUT
            for _ in range(line.accepted_qty)
        ]
        if buyout:
            requests.append(
                AcquisitionCreate(
                    type=AcquisitionType.BUYOUT,
                    contact_id=batch.contact_id,
                    note=note,
                    items=buyout,
                    payout_method=payout_method,
                )
            )
        consigned = [
            item(line, True)
            for line in accepted
            if line.acquisition_type is AcquisitionType.CONSIGNMENT
            for _ in range(line.accepted_qty)
        ]
        if consigned:
            requests.append(
                AcquisitionCreate(
                    type=AcquisitionType.CONSIGNMENT,
                    contact_id=batch.contact_id,
                    note=note,
                    items=consigned,
                )
            )
        for line in accepted:
            if line.acquisition_type is not AcquisitionType.BULK_LOT:
                continue
            assert line.deal_cost is not None and line.expected_listed_price is not None
            requests.append(
                AcquisitionCreate(
                    type=AcquisitionType.BULK_LOT,
                    contact_id=batch.contact_id,
                    note=note,
                    lot=AcquisitionLotIn(
                        name=line.short_name,
                        acquisition_cost=Decimal(line.deal_cost) * line.accepted_qty,
                        acquisition_basis=BulkAcquisitionBasis.UNSPECIFIED,
                        total_qty=line.accepted_qty,
                        unit_price=line.expected_listed_price,
                        retail_price=line.reference_price,
                        brand_id=line.brand_id,
                        category_id=line.category_id,
                        note=line.note,
                    ),
                    payout_method=payout_method,
                )
            )
        return requests

    # ── 查詢 ──────────────────────────────────────────────────────────

    async def receipt(self, store_id: int, batch_id: int) -> IntakeReceiptRead:
        """整批的收購明細（含簽名）。付款後、且付款時有客人簽名才印得出來。"""
        batch = await self._batch(store_id, batch_id)
        if batch.status not in _PAID_STATUSES:
            raise IntakeConflict("付款後才能印收購明細")
        task = (
            await SigningService(self._session).get_task(store_id, batch.signature_task_id)
            if batch.signature_task_id is not None
            else None
        )
        if (
            task is None
            or task.status is not SignatureTaskStatus.CONSUMED
            or task.signed_at is None
            or task.chosen_payout is None
        ):
            raise IntakeConflict("這一批付款時沒有請客人簽名，沒有收購明細（含簽名）可印")
        acquisition_ids = sorted(
            (await self._repo.acquisition_ids_for(store_id, [batch.id])).get(batch.id, [])
        )
        acquisitions = AcquisitionService(self._session)
        for acquisition_id in acquisition_ids:
            found = await acquisitions.receipt_for_reprint(store_id, acquisition_id)
            if found is not None and found.voided_at is not None:
                raise IntakeConflict(f"收購單 #{acquisition_id} 已作廢，不印收購明細")
        granted, balance_after = await self._credit_facts(store_id, acquisition_ids)
        content = task.content
        raw_items = content.get("items")
        items = [
            IntakeReceiptItem(name=str(item.get("name", "")), amount=str(item.get("amount", "")))
            for item in (raw_items if isinstance(raw_items, list) else [])
            if isinstance(item, dict)
        ]
        numbers = "、".join(f"#{n}" for n in acquisition_ids)
        return IntakeReceiptRead(
            store_id=store_id,
            acquisition_id=acquisition_ids[0],
            reference=f"排隊收購 {ticket_label(batch.ticket_no)}，收購單 {numbers}",
            seller_name=str(content.get("seller_name", "")),
            items=items,
            total=str(content.get("total", "")),
            payout_method=task.chosen_payout,
            signed_at=task.signed_at,
            signature_task_id=task.id,
            store_credit_granted=granted,
            store_credit_balance_after=balance_after,
        )

    async def _credit_facts(
        self, store_id: int, acquisition_ids: list[int]
    ) -> tuple[Decimal | None, Decimal | None]:
        """整批撥入的購物金加總，與最後一筆撥入後的帳本餘額；沒撥購物金回 (None, None)。"""
        storecredit = StoreCreditService(self._session)
        entries = [
            entry
            for acquisition_id in acquisition_ids
            if (
                entry := await storecredit.find_entry_by_source(
                    store_id,
                    StoreCreditSourceType.ACQUISITION,
                    acquisition_id,
                    StoreCreditEntryType.CREDIT,
                )
            )
            is not None
        ]
        if not entries:
            return None, None
        latest = max(entries, key=lambda entry: entry.id)
        return sum((Decimal(e.signed_amount) for e in entries), Decimal(0)), Decimal(
            latest.balance_after
        )

    async def get_batch(self, store_id: int, batch_id: int) -> IntakeBatch:
        return await self._batch(store_id, batch_id)

    async def list_batches(
        self, store_id: int, *, include_closed: bool, limit: int, offset: int
    ) -> list[IntakeBatch]:
        """佇列：預設只列還沒處理完的（到簽署為止）；先到先處理。"""
        return await self._repo.list_batches(
            store_id, None if include_closed else OPEN_STATUSES, limit=limit, offset=offset
        )

    async def to_reads(self, store_id: int, batches: list[IntakeBatch]) -> list[IntakeBatchRead]:
        """API 輸出：附賣方姓名、各列與總額（一次查完，不逐批查）。"""
        names = await self._contacts.names_for(store_id, list({b.contact_id for b in batches}))
        acquisitions = await self._repo.acquisition_ids_for(store_id, [b.id for b in batches])
        lines_by_batch: dict[int, list[IntakeLine]] = {}
        for line in await self._repo.lines_for(store_id, [b.id for b in batches]):
            lines_by_batch.setdefault(line.batch_id, []).append(line)
        return [
            self._read(
                batch,
                names.get(batch.contact_id, ""),
                lines_by_batch.get(batch.id, []),
                acquisitions.get(batch.id, []),
            )
            for batch in batches
        ]

    async def to_read(self, store_id: int, batch: IntakeBatch) -> IntakeBatchRead:
        return (await self.to_reads(store_id, [batch]))[0]

    # ── 內部 ──────────────────────────────────────────────────────────

    @staticmethod
    def _read(
        batch: IntakeBatch, contact_name: str, lines: list[IntakeLine], acquisition_ids: list[int]
    ) -> IntakeBatchRead:
        def cost(line: IntakeLine, qty: int) -> Decimal:
            if line.acquisition_type is AcquisitionType.CONSIGNMENT or line.deal_cost is None:
                return Decimal(0)  # 寄售不付收購款
            return Decimal(line.deal_cost) * qty

        accepted = [line for line in lines if line.disposition is IntakeDisposition.ACCEPTED]
        return IntakeBatchRead(
            id=batch.id,
            ticket_date=batch.ticket_date,
            ticket_no=batch.ticket_no,
            ticket_label=ticket_label(batch.ticket_no),
            slip_code=slip_code(batch.id),
            contact_id=batch.contact_id,
            contact_name=contact_name,
            declared_item_count=batch.declared_item_count,
            status=batch.status,
            note=batch.note,
            created_at=batch.created_at,
            cancel_reason=batch.cancel_reason,
            line_count=len(lines),
            item_count=sum(line.qty for line in lines),
            deal_total=sum((cost(line, line.qty) for line in lines), Decimal(0)),
            accepted_item_count=sum(line.accepted_qty for line in accepted),
            accepted_total=sum((cost(line, line.accepted_qty) for line in accepted), Decimal(0)),
            signature_task_id=batch.signature_task_id,
            paid_at=batch.paid_at,
            acquisition_ids=acquisition_ids,
            lines=[IntakeLineRead.model_validate(line, from_attributes=True) for line in lines],
        )

    async def _batch(
        self, store_id: int, batch_id: int, *, for_update: bool = False
    ) -> IntakeBatch:
        batch = await self._repo.get_batch(store_id, batch_id, for_update=for_update)
        if batch is None:
            raise IntakeBatchNotFound(f"找不到這一批（id={batch_id}）")
        return batch

    async def _editable_batch(self, store_id: int, batch_id: int) -> IntakeBatch:
        batch = await self._batch(store_id, batch_id, for_update=True)
        if batch.status not in _EDITABLE:
            raise IntakeConflict("這一批已經取消、簽署或付款，估價不能再改")
        return batch

    async def _line(self, store_id: int, batch_id: int, line_id: int) -> IntakeLine:
        line = await self._repo.get_line(store_id, batch_id, line_id)
        if line is None:
            raise IntakeBatchNotFound(f"找不到這一列（id={line_id}）")
        return line

    async def _check_line(self, store_id: int, line: IntakeLine) -> None:
        """欄位一致性（schema 管不到的）：簡稱不可空白、折數要有原價、寄售要有抽成、參照屬本店。"""
        line.short_name = line.short_name.strip()
        if not line.short_name:
            raise InvalidIntakeLine("請填商品簡稱（上架時才認得出是哪一件）")
        if line.note is not None:
            line.note = line.note.strip() or None
        if line.discount_pct is not None and line.reference_price is None:
            raise InvalidIntakeLine("用折數估價要先填原價")
        if line.acquisition_type is AcquisitionType.CONSIGNMENT:
            if line.commission_pct is None:
                raise InvalidIntakeLine("寄售要填抽成 %")
        elif line.commission_pct is not None:
            raise InvalidIntakeLine("只有寄售有抽成 %")
        checks = [
            (line.category_id, self._inventory.get_category, "分類"),
            (line.brand_id, self._inventory.get_brand, "品牌"),
            (line.product_model_id, self._inventory.get_product_model, "型號"),
        ]
        for ref_id, getter, label in checks:
            if ref_id is not None and await getter(store_id, ref_id) is None:
                raise InvalidIntakeLine(f"{label}不存在或不屬於本店")

    @staticmethod
    def _accepted_qty(line: IntakeLine, request: IntakeDispositionRequest) -> int:
        if request.disposition is IntakeDisposition.ACCEPTED:
            accepted = line.qty if request.accepted_qty is None else request.accepted_qty
            if not 1 <= accepted <= line.qty:
                raise InvalidIntakeLine(f"接受的件數要在 1 到 {line.qty} 件之間")
            return accepted
        if request.accepted_qty:
            raise InvalidIntakeLine("沒有接受的列，接受件數必須是 0")
        return 0
