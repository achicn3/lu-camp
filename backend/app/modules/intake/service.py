"""收購佇列 service（docs/42）：報到收件、估價、叫號確認、取消。業務規則集中於此。

計價分工沿用收購頁（docs/42 §11）：建議收購價與預計售價由前端同一套 `pricing.ts` 算好送來，
後端只驗金額合法與欄位一致，不重算建議價——成交價本來就可由店員改（裁示 5）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import store_date, utc_now
from app.modules.contacts.service import ContactService
from app.modules.intake.models import IntakeBatch, IntakeLine
from app.modules.intake.repository import IntakeRepository
from app.modules.intake.schemas import (
    IntakeBatchRead,
    IntakeDispositionRequest,
    IntakeLineFields,
    IntakeLineRead,
)
from app.modules.inventory.service import InventoryService
from app.shared.enums import AcquisitionType, IntakeBatchStatus, IntakeDisposition
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

    # ── 查詢 ──────────────────────────────────────────────────────────

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
        lines_by_batch: dict[int, list[IntakeLine]] = {}
        for line in await self._repo.lines_for(store_id, [b.id for b in batches]):
            lines_by_batch.setdefault(line.batch_id, []).append(line)
        return [
            self._read(batch, names.get(batch.contact_id, ""), lines_by_batch.get(batch.id, []))
            for batch in batches
        ]

    async def to_read(self, store_id: int, batch: IntakeBatch) -> IntakeBatchRead:
        return (await self.to_reads(store_id, [batch]))[0]

    # ── 內部 ──────────────────────────────────────────────────────────

    @staticmethod
    def _read(batch: IntakeBatch, contact_name: str, lines: list[IntakeLine]) -> IntakeBatchRead:
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
