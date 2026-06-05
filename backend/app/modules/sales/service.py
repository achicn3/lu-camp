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
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.models import Sale, SaleLine
from app.modules.sales.repository import SalesRepository
from app.modules.settings.service import StoreSettingsService
from app.modules.user.service import UserService
from app.shared.enums import (
    CashMovementType,
    ItemKind,
    OwnershipType,
    SaleInvoiceStatus,
    SaleLineType,
    StockReason,
)
from app.shared.exceptions import (
    CrossStoreReference,
    EmptySale,
    IdempotencyKeyConflict,
    NoOpenCashSession,
    SaleAlreadyVoid,
    SaleItemNotFound,
    SaleLineInvalid,
)


def _cart_fingerprint(lines: list[SaleLineInput], buyer_contact_id: int | None) -> str:
    """購物車內容的穩定 sha256；供 idempotency 重播時比對請求是否相同。"""
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

    async def create_sale(
        self,
        store_id: int,
        clerk_user_id: int,
        *,
        lines: list[SaleLineInput],
        buyer_contact_id: int | None = None,
        idempotency_key: str | None = None,
    ) -> Sale:
        """建立銷售單並完成扣庫存/收現/結算；任一步失敗整筆回復（不 commit）。

        idempotency（D-2）：帶 idempotency_key 時，若同 (store_id, key) 已有銷售 → 直接回原單、
        不重跑任何副作用（防網路重試重複建單/收錢）。並行重送的競態由 sales 的
        (store_id, idempotency_key) 唯一約束在 flush/commit 擋下，由呼叫端據此回原單。
        """
        if not lines:
            raise EmptySale("銷售單必須至少有一筆明細")

        fingerprint = _cart_fingerprint(lines, buyer_contact_id)

        # idempotent replay：已存在同 key 的銷售 → 內容相同回原單、不再產生副作用；
        # 內容不同則拒絕（避免誤用/重用 key 把不同購物車的結帳靜默丟掉）。
        if idempotency_key is not None:
            replay = await self.find_idempotent_replay(
                store_id, idempotency_key, lines=lines, buyer_contact_id=buyer_contact_id
            )
            if replay is not None:
                return replay

        # 收現必須在開帳中（§7.8）：最先檢查，避免動了庫存才發現不能收錢。
        if await self._cash.get_current_session(store_id) is None:
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

        for line in lines:
            line_total = await self._process_line(store_id, sale.id, line, consignment_sales)
            total += line_total

        # 稅於發票總額層級推算一次（§6）；不逐項算稅。
        tax_rate = (await self._settings.get_effective_settings(store_id)).tax_rate
        net, tax = split_tax_inclusive(total, tax_rate)
        sale.subtotal = Decimal(net)
        sale.tax = Decimal(tax)
        sale.total = total
        await self._session.flush()

        # 收現進帳（必在開帳中；上方已確認）。
        await self._cash.record_movement(
            store_id,
            CashMovementType.SALE_IN,
            total,
            actor_user_id=clerk_user_id,
            ref_type="sale",
            ref_id=sale.id,
        )

        # 寄售品 → 建 PENDING 結算（店家收入只認抽成，§7.3）。
        for serialized_item_id, gross, commission_pct in consignment_sales:
            await self._consignment.create_settlement(
                store_id,
                serialized_item_id=serialized_item_id,
                sale_id=sale.id,
                gross=gross,
                commission_pct=commission_pct,
            )

        await self._session.flush()
        return sale

    async def find_idempotent_replay(
        self,
        store_id: int,
        idempotency_key: str,
        *,
        lines: list[SaleLineInput],
        buyer_contact_id: int | None,
    ) -> Sale | None:
        """同 key 且購物車相符 → 回原單；內容不符 → IdempotencyKeyConflict；不存在 → None。

        pre-check（create_sale）與 router 的 IntegrityError handler（並行重送）共用此處，
        避免「修一條路徑、漏另一條」導致併發同 key 不同購物車仍被靜默當成功。
        """
        existing = await self._repo.get_by_idempotency_key(store_id, idempotency_key)
        if existing is None:
            return None
        if existing.idempotency_fingerprint != _cart_fingerprint(lines, buyer_contact_id):
            raise IdempotencyKeyConflict(
                f"idempotency key 已用於不同的購物車內容（sale {existing.id}）"
            )
        return existing

    # ── 查詢 ──
    async def get_sale(self, store_id: int, sale_id: int) -> Sale | None:
        return await self._repo.get_sale(store_id, sale_id)

    async def get_lines(self, sale_id: int) -> list[SaleLine]:
        return await self._repo.list_lines(sale_id)

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

    # ── 作廢 ──
    async def void_sale(self, sale: Sale, actor_user_id: int) -> Sale:
        """作廢銷售：標記 invoice_status=VOID（待作廢），寫稽核；不刪除、不在此反轉庫存/現金。

        若原銷售已開發票（invoice_status=ISSUED），此 VOID 為「作廢發票流程」的接縫——實際
        電子發票作廢 XML 由 T13/T14 處理。實體退貨/退現/折讓屬 Phase 4 returns（§7.5），不在此。

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
