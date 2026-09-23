"""散裝販售籃（ADR-025）：多次收購共用一張標籤與一個每件售價。

不變量：

- 籃子只是販售品項；庫存與成本留在各來源 BulkLot，每次收購一筆、互不覆寫。
- 可售數量＝籃內販售中來源的 remaining_qty 加總，不另存一份數字。
- 同籃一個價：來源的名稱／品牌／分類／售價都跟著籃子，來源不能自己改價。
- 只收自有散裝；寄售散裝有各自的寄售人與分潤，不可混進同一籃。
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.money import round_ntd
from app.modules.inventory.basket_repository import BulkBasketRepository
from app.modules.inventory.models import BulkBasket, BulkLot
from app.modules.inventory.repository import InventoryRepository
from app.modules.inventory.service import InventoryService
from app.shared.enums import BulkLotStatus
from app.shared.exceptions import BulkBasketConflict, BulkBasketNotFound, InsufficientStock

_CODE_RANDOM_LEN = 10
# 籃子跟著來源同步的欄位：同籃同品項，這四項不一致就等於一張標籤兩種東西。
_MIRRORED_FIELDS = ("name", "brand_id", "category_id", "unit_price")
# 售價改動只同步到還可能再賣的來源；WRITTEN_OFF 是作廢收購退場的，不再動它。
_REPRICEABLE = (BulkLotStatus.ON_SALE, BulkLotStatus.SOLD_OUT)


def new_basket_code(store_id: int) -> str:
    """販售籃識別碼，如 ``K1-3F9A2B7C4D``（同 lot_code 的 Code 128 可編碼字元集）。"""
    return f"K{store_id}-{uuid4().hex[:_CODE_RANDOM_LEN].upper()}"


def unit_cost(lot: BulkLot) -> int:
    """單件成本＝整批成本 ÷ 原數量，整數元 HALF_UP（估價參考用）。"""
    return round_ntd(lot.acquisition_cost / Decimal(lot.total_qty))


@dataclass(frozen=True)
class BasketView:
    """籃子＋其來源的唯讀組合（router 轉成 BulkBasketRead）。"""

    basket: BulkBasket
    sources: list[BulkLot] = field(default_factory=list)

    @property
    def remaining_qty(self) -> int:
        return sum(s.remaining_qty for s in self.sources if s.status == BulkLotStatus.ON_SALE)

    @property
    def cost_reference(self) -> dict[str, Any]:
        costs = [unit_cost(s) for s in self.sources]
        return {
            "sample_count": len(costs),
            "unit_cost_min": Decimal(min(costs)) if costs else None,
            "unit_cost_max": Decimal(max(costs)) if costs else None,
        }


class BulkBasketService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = BulkBasketRepository(session)
        self._inventory_repo = InventoryRepository(session)
        self._inventory = InventoryService(session)

    # ── 查詢 ──

    async def get(self, store_id: int, basket_id: int) -> BasketView | None:
        basket = await self._repo.get(store_id, basket_id)
        return None if basket is None else await self._view(basket)

    async def get_by_code(self, store_id: int, code: str) -> BasketView | None:
        """POS 掃籃子標籤。"""
        basket = await self._repo.get_by_code(store_id, code)
        return None if basket is None else await self._view(basket)

    async def list_baskets(
        self, store_id: int, *, q: str | None = None, include_inactive: bool = False
    ) -> list[BasketView]:
        baskets = await self._repo.list_baskets(store_id, q=q, include_inactive=include_inactive)
        grouped = await self._repo.sources_by_basket(store_id, [b.id for b in baskets])
        return [BasketView(b, grouped[b.id]) for b in baskets]

    async def _view(self, basket: BulkBasket) -> BasketView:
        grouped = await self._repo.sources_by_basket(basket.store_id, [basket.id])
        return BasketView(basket, grouped[basket.id])

    # ── 建立 ──

    async def create(
        self,
        store_id: int,
        *,
        name: str,
        unit_price: Decimal,
        brand_id: int | None = None,
        category_id: int | None = None,
        note: str | None = None,
        actor_user_id: int,
    ) -> BasketView:
        """建立空籃（之後由收購或「加入既有散裝」補進來源）。"""
        await self._inventory.validate_item_references(
            store_id, brand_id=brand_id, category_id=category_id
        )
        basket = await self._repo.add(
            BulkBasket(
                store_id=store_id,
                code=new_basket_code(store_id),
                name=name,
                unit_price=Decimal(round_ntd(unit_price)),
                brand_id=brand_id,
                category_id=category_id,
                note=note,
            )
        )
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="CREATE_BULK_BASKET",
            entity_type="bulk_basket",
            entity_id=str(basket.id),
            after={"name": name, "unit_price": str(basket.unit_price)},
        )
        return BasketView(basket)

    # ── 收購入籃 ──

    async def lock_for_intake(self, store_id: int, basket_id: int) -> BulkBasket:
        """收購要加入的籃：鎖列並確認可收。他店／不存在 → NotFound；已停用 → Conflict。"""
        basket = await self._repo.get_for_update(store_id, basket_id)
        if basket is None:
            raise BulkBasketNotFound(f"找不到販售籃 {basket_id}")
        if not basket.is_active:
            raise BulkBasketConflict(
                f"販售籃「{basket.name}」已設為不再加入收購；要加請先到庫存頁恢復"
            )
        return basket

    async def attach_new_lot(self, basket: BulkBasket, lot: BulkLot) -> None:
        """剛建立的來源掛進籃子（呼叫端已用 lock_for_intake 取得並鎖住籃子）。"""
        self._mirror(basket, lot)
        lot.basket_id = basket.id
        await self._session.flush()

    async def create_from_lot(self, lot: BulkLot, *, actor_user_id: int) -> BulkBasket:
        """收購時「建立新販售籃」：以這筆來源的名稱／品牌／分類／售價開籃。"""
        view = await self.create(
            lot.store_id,
            name=lot.name,
            unit_price=lot.unit_price,
            brand_id=lot.brand_id,
            category_id=lot.category_id,
            actor_user_id=actor_user_id,
        )
        lot.basket_id = view.basket.id
        await self._session.flush()
        return view.basket

    async def code_of(self, store_id: int, basket_id: int | None) -> str | None:
        if basket_id is None:
            return None
        basket = await self._repo.get(store_id, basket_id)
        return None if basket is None else basket.code

    # ── 結帳 ──

    async def sell(
        self, store_id: int, basket_id: int, qty: int
    ) -> tuple[BulkBasket, list[tuple[BulkLot, int]]]:
        """自籃子售出 qty 件：依入庫先後（FIFO）扣各來源，回 (籃子, [(來源, 件數)…])。

        同一交易內先鎖籃、再依 id 鎖全部來源，與改價／入籃／其他結帳序列化；
        可售不足整筆拒絕（InsufficientStock），不會留下部分扣減。
        """
        if qty <= 0:
            raise InsufficientStock("售出數量必須 > 0")
        basket = await self._repo.get_for_update(store_id, basket_id)
        if basket is None:
            raise BulkBasketNotFound(f"找不到販售籃 {basket_id}")
        locked = await self._repo.sources_for_update(store_id, basket.id)
        sellable = sorted(
            (
                lot
                for lot in locked
                if lot.status == BulkLotStatus.ON_SALE and lot.remaining_qty > 0
            ),
            key=lambda lot: (lot.intake_date, lot.id),
        )
        if sum(lot.remaining_qty for lot in sellable) < qty:
            raise InsufficientStock(f"販售籃「{basket.name}」庫存不足")
        parts: list[tuple[BulkLot, int]] = []
        need = qty
        for lot in sellable:
            if need == 0:
                break
            take = min(lot.remaining_qty, need)
            await self._inventory.sell_bulk_lot_items(lot.id, take)
            parts.append((lot, take))
            need -= take
        return basket, parts

    async def prelock_for_sale(
        self, store_id: int, basket_ids: list[int], lot_ids: list[int]
    ) -> None:
        """結帳前依 id 鎖定本單用到的籃子與散裝來源（防兩台收銀反序互卡）。"""
        await self._repo.lock_for_sale(store_id, sorted(set(basket_ids)), sorted(set(lot_ids)))

    # ── 管理 ──

    async def add_existing_lot(
        self, store_id: int, basket_id: int, lot_id: int, *, actor_user_id: int
    ) -> BasketView:
        """既有散裝整批加入籃：須自有、未入其他籃、未作廢，且與籃子同價（裁示 2026-09-22）。"""
        basket = await self._repo.get_for_update(store_id, basket_id)
        if basket is None:
            raise BulkBasketNotFound(f"找不到販售籃 {basket_id}")
        lot = await self._inventory_repo.get_bulk_lot_for_update(store_id, lot_id)
        if lot is None:
            raise BulkBasketNotFound(f"找不到散裝 {lot_id}")
        if lot.basket_id is not None:
            raise BulkBasketConflict(f"散裝 {lot.lot_code} 已經在販售籃裡")
        if lot.consignor_id is not None:
            raise BulkBasketConflict("寄售的散裝不能放進販售籃（寄售人與分潤各自不同）")
        if lot.status == BulkLotStatus.WRITTEN_OFF:
            raise BulkBasketConflict(f"散裝 {lot.lot_code} 的收購已作廢")
        if lot.unit_price != basket.unit_price:
            raise BulkBasketConflict(
                f"售價不同（這批 {lot.unit_price} 元、籃子 {basket.unit_price} 元），"
                "同價才能放同一籃；請先把其中一邊改成相同售價"
            )
        before = {key: _jsonable(getattr(lot, key)) for key in _MIRRORED_FIELDS}
        self._mirror(basket, lot)
        lot.basket_id = basket.id
        await self._session.flush()
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="ADD_LOT_TO_BULK_BASKET",
            entity_type="bulk_basket",
            entity_id=str(basket.id),
            before={"bulk_lot_id": lot.id, **before},
            after={"bulk_lot_id": lot.id, "basket_id": basket.id},
        )
        return await self._view(basket)

    async def update(
        self, store_id: int, basket_id: int, changes: dict[str, Any], *, actor_user_id: int
    ) -> BasketView | None:
        """改籃子（限管理者）。名稱／品牌／分類／售價同步到籃內來源；寫稽核。"""
        basket = await self._repo.get_for_update(store_id, basket_id)
        if basket is None:
            return None
        await self._inventory.validate_item_references(
            store_id,
            brand_id=changes.get("brand_id", basket.brand_id),
            category_id=changes.get("category_id", basket.category_id),
        )
        if "unit_price" in changes:
            changes["unit_price"] = Decimal(round_ntd(changes["unit_price"]))
        before: dict[str, object] = {}
        after: dict[str, object] = {}
        for key, value in changes.items():
            old = getattr(basket, key)
            if old == value:
                continue
            before[key] = _jsonable(old)
            after[key] = _jsonable(value)
            setattr(basket, key, value)
        if not after:
            return await self._view(basket)
        if any(key in after for key in _MIRRORED_FIELDS):
            for lot in await self._repo.sources_for_update(store_id, basket.id):
                if lot.status in _REPRICEABLE:
                    self._mirror(basket, lot)
        await self._session.flush()
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="UPDATE_BULK_BASKET",
            entity_type="bulk_basket",
            entity_id=str(basket.id),
            before=before,
            after=after,
        )
        return await self._view(basket)

    @staticmethod
    def _mirror(basket: BulkBasket, lot: BulkLot) -> None:
        for key in _MIRRORED_FIELDS:
            setattr(lot, key, getattr(basket, key))


def _jsonable(value: object) -> object:
    return str(value) if isinstance(value, Decimal) else value
