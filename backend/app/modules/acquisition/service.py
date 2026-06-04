"""acquisition 業務邏輯：收購/寄售入庫的單交易 orchestrate。

於單一 DB 交易內協調 contacts / inventory / cashdrawer 三個 service：
任一步失敗即整筆回復（本層只 flush、不 commit；commit/rollback 由 router 控制），
不會留下「庫存建了但現金沒扣」之類的半套。跨模組一律經對方 service，不碰其 repository。

守門：
- 收購對象必須存在且有 national_id（接 T4 的 SELLER/CONSIGNOR 必填）。
- 付現類型（BUYOUT/BULK_LOT）必須在開帳中的 cash_session 下進行（§7.8），否則整筆擋。
金額一律 Decimal/整數元（core/money），無 float。
"""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import round_ntd
from app.modules.acquisition.codes import new_item_code, new_lot_code
from app.modules.acquisition.models import Acquisition
from app.modules.acquisition.repository import AcquisitionRepository
from app.modules.acquisition.schemas import AcquisitionCreate, AcquisitionResult
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.service import ContactService
from app.modules.inventory.service import InventoryService
from app.shared.enums import (
    AcquisitionType,
    CashMovementType,
    Grade,
    ItemKind,
    OwnershipType,
    StockReason,
)
from app.shared.exceptions import (
    AcquisitionRequiresNationalId,
    ContactNotFound,
    InvalidCommissionPct,
    NoOpenCashSession,
)

COMMISSION_PCT_MIN = 0
COMMISSION_PCT_MAX = 100
_CASH_PAYING = frozenset({AcquisitionType.BUYOUT, AcquisitionType.BULK_LOT})


class AcquisitionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = AcquisitionRepository(session)
        self._contacts = ContactService(session)
        self._inventory = InventoryService(session)
        self._cash = CashDrawerService(session)

    async def get_acquisition(self, store_id: int, acquisition_id: int) -> Acquisition | None:
        return await self._repo.get(store_id, acquisition_id)

    async def create_acquisition(
        self, store_id: int, clerk_user_id: int, data: AcquisitionCreate
    ) -> AcquisitionResult:
        """建立收購單並完成入庫/付現；任一步失敗整筆回復（不 commit）。"""
        contact = await self._contacts.get_contact(store_id, data.contact_id)
        if contact is None:
            raise ContactNotFound(f"找不到 contact {data.contact_id}")
        if contact.national_id_enc is None:
            raise AcquisitionRequiresNationalId("收購/寄售對象必須有 national_id")

        pays_cash = data.type in _CASH_PAYING
        if pays_cash and await self._cash.get_current_session(store_id) is None:
            raise NoOpenCashSession("收購付現必須在開帳中的 cash_session 下進行，請先開帳")

        acquisition = await self._repo.add(
            Acquisition(
                store_id=store_id,
                type=data.type,
                contact_id=contact.id,
                clerk_user_id=clerk_user_id,
                note=data.note,
            )
        )

        if data.type == AcquisitionType.BULK_LOT:
            lot_code, total_cash = await self._create_bulk_lot(store_id, acquisition.id, data)
            item_codes: list[str] = []
        else:
            item_codes, total_cash = await self._create_serialized_items(
                store_id, contact.id, acquisition.id, data
            )
            lot_code = None

        if pays_cash:
            paid = Decimal(round_ntd(total_cash))
            acquisition.total_cash_paid = paid
            await self._cash.record_movement(
                store_id,
                CashMovementType.BUYOUT_OUT,
                paid,
                actor_user_id=clerk_user_id,
                ref_type="acquisition",
                ref_id=acquisition.id,
            )
            await self._session.flush()

        return AcquisitionResult(
            acquisition_id=acquisition.id,
            type=data.type,
            contact_id=contact.id,
            total_cash_paid=acquisition.total_cash_paid,
            item_codes=item_codes,
            lot_code=lot_code,
        )

    async def _create_serialized_items(
        self, store_id: int, contact_id: int, acquisition_id: int, data: AcquisitionCreate
    ) -> tuple[list[str], Decimal]:
        assert data.items is not None  # schema 已驗證
        item_codes: list[str] = []
        total_cash = Decimal(0)
        for item in data.items:
            code = new_item_code(store_id)
            if data.type == AcquisitionType.BUYOUT:
                ownership = OwnershipType.OWNED
                consignor_id: int | None = None
                commission: int | None = None
                cost: Decimal | None = item.acquisition_cost
                assert cost is not None  # schema 已驗證
                total_cash += cost
            else:  # CONSIGNMENT
                ownership = OwnershipType.CONSIGNMENT
                consignor_id = contact_id
                commission = item.commission_pct
                assert commission is not None  # schema 已驗證
                if not COMMISSION_PCT_MIN <= commission <= COMMISSION_PCT_MAX:
                    raise InvalidCommissionPct(
                        f"commission_pct 須介於 {COMMISSION_PCT_MIN}-{COMMISSION_PCT_MAX}"
                    )
                cost = None
            created = await self._inventory.create_serialized_item(
                store_id,
                item_code=code,
                name=item.name,
                grade=item.grade,
                ownership_type=ownership,
                listed_price=item.listed_price,
                brand_id=item.brand_id,
                product_model_id=item.product_model_id,
                acquisition_cost=cost,
                consignor_id=consignor_id,
                commission_pct=commission,
                acquisition_id=acquisition_id,
            )
            await self._inventory.record_stock_in(
                store_id,
                ItemKind.SERIALIZED,
                qty=1,
                reason=StockReason.ACQUISITION,
                ref_type="acquisition",
                ref_id=acquisition_id,
                serialized_item_id=created.id,
            )
            item_codes.append(code)
        return item_codes, total_cash

    async def _create_bulk_lot(
        self, store_id: int, acquisition_id: int, data: AcquisitionCreate
    ) -> tuple[str, Decimal]:
        lot = data.lot
        assert lot is not None  # schema 已驗證
        lot_code = new_lot_code(store_id)
        created = await self._inventory.create_bulk_lot(
            store_id,
            lot_code=lot_code,
            name=lot.name,
            grade=Grade.E,
            acquisition_cost=lot.acquisition_cost,
            acquisition_basis=lot.acquisition_basis,
            unit_price=lot.unit_price,
            total_qty=lot.total_qty,
            brand_id=lot.brand_id,
            label=lot.label,
            acquisition_id=acquisition_id,
        )
        await self._inventory.record_stock_in(
            store_id,
            ItemKind.BULK_LOT,
            qty=lot.total_qty,
            reason=StockReason.ACQUISITION,
            ref_type="acquisition",
            ref_id=acquisition_id,
            bulk_lot_id=created.id,
        )
        return lot_code, lot.acquisition_cost
