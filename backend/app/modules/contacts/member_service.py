"""會員中心 facade（T21-c；docs/17 §2、§5）：唯讀彙整協調者。

沿 ReportsService 慣例：只透過對方 service 取數（不直接碰他模組資料表，CLAUDE.md §2），
回傳 Pydantic 讀取 schema。跨模組邊界：寄售人↔結算以 inventory 提供的 serialized_item_ids
串接（consignment 不查 inventory 表）；買斷來源以 acquisition 的收購單 id 反查 inventory。

contact 不存在（含跨店）一律回 None → router 轉 404。彙整一律分頁/加總，勿 eager load 全史。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.acquisition.service import AcquisitionService
from app.modules.consignment.models import ConsignmentSettlement
from app.modules.consignment.service import ConsignmentService
from app.modules.contacts.member_schemas import (
    MemberConsignmentRead,
    MemberConsignmentsRead,
    MemberOverviewCounts,
    MemberOverviewRead,
    MemberPurchaseDetailRead,
    MemberPurchaseLineRead,
    MemberPurchaseRead,
    MemberPurchaseTenderRead,
    MemberSourcedItemRead,
)
from app.modules.contacts.schemas import ContactRead
from app.modules.contacts.service import ContactService
from app.modules.inventory.models import BulkLot, SerializedItem
from app.modules.inventory.service import InventoryService
from app.modules.sales.models import Sale
from app.modules.sales.service import SalesService
from app.modules.storecredit.service import StoreCreditService
from app.shared.enums import BulkLotStatus, SerializedItemStatus

_RECENT_PURCHASES = 5


def _as_serialized_status(value: str | None) -> SerializedItemStatus | None:
    if value is None:
        return None
    try:
        return SerializedItemStatus(value)
    except ValueError:
        return None


def _as_bulk_status(value: str | None) -> BulkLotStatus | None:
    if value is None:
        return None
    try:
        return BulkLotStatus(value)
    except ValueError:
        return None


class MemberService:
    def __init__(self, session: AsyncSession) -> None:
        self._contacts = ContactService(session)
        self._store_credit = StoreCreditService(session)
        self._sales = SalesService(session)
        self._inventory = InventoryService(session)
        self._acquisition = AcquisitionService(session)
        self._consignment = ConsignmentService(session)

    # ── overview ──

    async def get_overview(self, store_id: int, contact_id: int) -> MemberOverviewRead | None:
        contact = await self._contacts.get_contact(store_id, contact_id)
        if contact is None:
            return None
        balance = await self._store_credit.get_balance(store_id, contact_id)
        pending = await self._pending_consignment_payout(store_id, contact_id)
        purchases_count = await self._sales.count_purchases_by_buyer(store_id, contact_id)
        consigned_count = await self._inventory.count_serialized_by_consignor(
            store_id, contact_id
        ) + await self._inventory.count_bulk_lots_by_consignor(store_id, contact_id)
        recent_sales = await self._sales.list_purchases_by_buyer(
            store_id, contact_id, limit=_RECENT_PURCHASES, offset=0
        )
        line_counts = await self._sales.line_counts_for_sales([s.id for s in recent_sales])
        return MemberOverviewRead(
            contact=ContactRead.from_model(contact),
            member_points=contact.member_points,
            store_credit_balance=balance,
            pending_consignment_payout=pending,
            counts=MemberOverviewCounts(
                purchases=purchases_count, consigned_items=consigned_count
            ),
            recent_purchases=[
                self._purchase_row(s, line_counts.get(s.id, 0)) for s in recent_sales
            ],
        )

    # ── purchases ──

    async def list_purchases(
        self,
        store_id: int,
        contact_id: int,
        *,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
        offset: int,
    ) -> list[MemberPurchaseRead] | None:
        if await self._contacts.get_contact(store_id, contact_id) is None:
            return None
        sales = await self._sales.list_purchases_by_buyer(
            store_id, contact_id, date_from=date_from, date_to=date_to, limit=limit, offset=offset
        )
        line_counts = await self._sales.line_counts_for_sales([s.id for s in sales])
        return [self._purchase_row(s, line_counts.get(s.id, 0)) for s in sales]

    async def get_purchase_detail(
        self, store_id: int, contact_id: int, sale_id: int
    ) -> MemberPurchaseDetailRead | None:
        if await self._contacts.get_contact(store_id, contact_id) is None:
            return None
        sale = await self._sales.get_sale(store_id, sale_id)
        if sale is None or sale.buyer_contact_id != contact_id:
            return None  # 非該會員的單 / 不存在 → 404
        lines = await self._sales.get_lines(sale_id)
        tenders = await self._sales.get_tenders(sale_id)
        return MemberPurchaseDetailRead(
            sale_id=sale.id,
            created_at=sale.created_at,
            subtotal=sale.subtotal,
            tax=sale.tax,
            total=sale.total,
            payment_method=sale.payment_method.value,
            status=sale.status.value,
            invoice_status=sale.invoice_status.value,
            lines=[
                MemberPurchaseLineRead(
                    line_type=line.line_type.value,
                    description=line.description,
                    qty=line.qty,
                    unit_price=line.unit_price,
                    line_total=line.line_total,
                )
                for line in lines
            ],
            tenders=[
                MemberPurchaseTenderRead(tender_type=t.tender_type.value, amount=t.amount)
                for t in tenders
            ],
        )

    @staticmethod
    def _purchase_row(sale: Sale, line_count: int) -> MemberPurchaseRead:
        return MemberPurchaseRead(
            sale_id=sale.id,
            created_at=sale.created_at,
            total=sale.total,
            payment_method=sale.payment_method.value,
            status=sale.status.value,
            invoice_status=sale.invoice_status.value,
            line_count=line_count,
        )

    # ── consignments ──

    async def list_consignments(
        self, store_id: int, contact_id: int, *, limit: int, offset: int
    ) -> MemberConsignmentsRead | None:
        if await self._contacts.get_contact(store_id, contact_id) is None:
            return None
        # 兩來源（序號+散裝）各取至 offset+limit，**合併後**才切片——分別切片會回傳至多
        # 2×limit 列、且後續頁錯漏（Codex review P2）。以 (intake_date, id) 排序求穩定全序。
        cap = offset + limit
        serialized = await self._inventory.list_serialized(
            store_id, consignor_id=contact_id, limit=cap, offset=0
        )
        bulk = await self._inventory.list_bulk_lots(
            store_id, consignor_id=contact_id, limit=cap, offset=0
        )
        latest = await self._latest_settlements(store_id, [i.id for i in serialized])
        # 確定性全序 (intake_date, 來源序, id)：與各來源 id desc 取列一致（同 intake_date
        # 時以 id 排序，跨來源以固定來源序），避免 tie-break 與截斷序不符（Codex review P2）。
        # 來源序：序號=0、散裝=1。
        keyed: list[tuple[datetime, int, int, MemberConsignmentRead]] = [
            (i.intake_date, 0, i.id, self._consignment_serialized_row(i, latest.get(i.id)))
            for i in serialized
        ]
        keyed += [
            (lot.intake_date, 1, lot.id, self._consignment_bulk_row(lot)) for lot in bulk
        ]
        # intake_date desc、來源序 asc、id desc（來源序保持遞增、不被整體 reverse 反轉）。
        keyed.sort(key=lambda t: (-t[0].timestamp(), t[1], -t[2]))
        items = [row for *_, row in keyed][offset : offset + limit]
        pending = await self._pending_consignment_payout(store_id, contact_id)
        return MemberConsignmentsRead(items=items, pending_payout_total=pending)

    async def _latest_settlements(
        self, store_id: int, serialized_item_ids: list[int]
    ) -> dict[int, ConsignmentSettlement]:
        """每序號品最新一筆結算（DISTINCT ON，一 SQL 取回，不會餓死其他品）。"""
        return await self._consignment.latest_settlement_by_item_ids(
            store_id, serialized_item_ids
        )

    async def _pending_consignment_payout(self, store_id: int, contact_id: int) -> Decimal:
        item_ids = await self._inventory.list_serialized_ids_by_consignor(store_id, contact_id)
        return await self._consignment.pending_payout_total_by_item_ids(store_id, item_ids)

    @staticmethod
    def _consignment_serialized_row(
        item: SerializedItem, settlement: ConsignmentSettlement | None
    ) -> MemberConsignmentRead:
        return MemberConsignmentRead(
            kind="SERIALIZED",
            code=item.item_code,
            name=item.name,
            item_status=item.status.value,
            commission_pct=item.commission_pct,
            gross=settlement.gross if settlement is not None else None,
            commission_amount=settlement.commission_amount if settlement is not None else None,
            payout_amount=settlement.payout_amount if settlement is not None else None,
            settlement_status=settlement.status.value if settlement is not None else None,
            sold_date=item.sold_date,
        )

    @staticmethod
    def _consignment_bulk_row(lot: BulkLot) -> MemberConsignmentRead:
        return MemberConsignmentRead(
            kind="BULK_LOT",
            code=lot.lot_code,
            name=lot.name,
            item_status=lot.status.value,
            commission_pct=None,
        )

    # ── sourced items（買斷 + 寄售 union）──

    async def list_sourced_items(
        self,
        store_id: int,
        contact_id: int,
        *,
        source_type: str | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> list[MemberSourcedItemRead] | None:
        if await self._contacts.get_contact(store_id, contact_id) is None:
            return None
        cap = offset + limit
        # 過濾一律下推 DB（status 在 LIMIT 之前），各來源取至 cap、合併排序後切片——
        # 在 Python 端先截斷再過濾會漏掉較舊的符合列（Codex review P2）。
        ser_status = _as_serialized_status(status)
        bulk_status = _as_bulk_status(status)
        # status 給定但不屬該來源的列舉 → 該來源不可能命中，整支跳過。
        serialized_ok = status is None or ser_status is not None
        bulk_ok = status is None or bulk_status is not None
        want_buyout = source_type in (None, "BUYOUT")
        want_consignment = source_type in (None, "CONSIGNMENT")
        acq_ids = (
            await self._acquisition.list_ids_by_contact(store_id, contact_id)
            if want_buyout
            else []
        )
        # 確定性全序 (intake_date, 來源序, id)，與各來源 id desc 取列一致（避免 tie-break
        # 與截斷序不符；Codex review P2）。來源序：買斷序號=0、買斷散裝=1、寄售序號=2、寄售散裝=3。
        keyed: list[tuple[datetime, int, int, MemberSourcedItemRead]] = []
        if want_buyout and serialized_ok:
            for item in await self._inventory.list_serialized_by_acquisitions(
                store_id, acq_ids, status=ser_status, limit=cap, offset=0
            ):
                keyed.append(
                    (item.intake_date, 0, item.id, self._sourced_serialized_row(item, "BUYOUT"))
                )
        if want_buyout and bulk_ok:
            for lot in await self._inventory.list_bulk_lots_by_acquisitions(
                store_id, acq_ids, status=bulk_status, limit=cap, offset=0
            ):
                keyed.append((lot.intake_date, 1, lot.id, self._sourced_bulk_row(lot, "BUYOUT")))
        if want_consignment and serialized_ok:
            for item in await self._inventory.list_serialized(
                store_id, consignor_id=contact_id, status=ser_status, limit=cap, offset=0
            ):
                row = self._sourced_serialized_row(item, "CONSIGNMENT")
                keyed.append((item.intake_date, 2, item.id, row))
        if want_consignment and bulk_ok:
            for lot in await self._inventory.list_bulk_lots(
                store_id, consignor_id=contact_id, status=bulk_status, limit=cap, offset=0
            ):
                keyed.append(
                    (lot.intake_date, 3, lot.id, self._sourced_bulk_row(lot, "CONSIGNMENT"))
                )
        # intake_date desc、來源序 asc、id desc（來源序保持遞增、不被整體 reverse 反轉）。
        keyed.sort(key=lambda t: (-t[0].timestamp(), t[1], -t[2]))
        return [row for *_, row in keyed][offset : offset + limit]

    @staticmethod
    def _sourced_serialized_row(item: SerializedItem, source_type: str) -> MemberSourcedItemRead:
        return MemberSourcedItemRead(
            source_type=source_type,
            kind="SERIALIZED",
            code=item.item_code,
            name=item.name,
            status=item.status.value,
            acquisition_id=item.acquisition_id,
            intake_date=item.intake_date,
            listed_price=item.listed_price,
        )

    @staticmethod
    def _sourced_bulk_row(lot: BulkLot, source_type: str) -> MemberSourcedItemRead:
        return MemberSourcedItemRead(
            source_type=source_type,
            kind="BULK_LOT",
            code=lot.lot_code,
            name=lot.name,
            status=lot.status.value,
            acquisition_id=lot.acquisition_id,
            intake_date=lot.intake_date,
            listed_price=lot.unit_price,
        )
