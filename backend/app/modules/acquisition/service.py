"""acquisition 業務邏輯：收購/寄售入庫的單交易 orchestrate。

於單一 DB 交易內協調 contacts / inventory / cashdrawer 三個 service：
任一步失敗即整筆回復（本層只 flush、不 commit；commit/rollback 由 router 控制），
不會留下「庫存建了但現金沒扣」之類的半套。跨模組一律經對方 service，不碰其 repository。

守門：
- 收購對象必須存在且有 national_id（接 T4 的 SELLER/CONSIGNOR 必填）。
- 付現類型（BUYOUT/BULK_LOT）必須在開帳中的 cash_session 下進行（§7.8），否則整筆擋。
金額一律 Decimal/整數元（core/money），無 float。
"""

import hashlib
import json
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.money import round_ntd
from app.modules.acquisition.codes import new_item_code, new_lot_code
from app.modules.acquisition.models import Acquisition
from app.modules.acquisition.repository import AcquisitionRepository
from app.modules.acquisition.schemas import AcquisitionCreate, AcquisitionResult
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.service import ContactService
from app.modules.inventory.service import InventoryService
from app.modules.settings.service import StoreSettingsService
from app.modules.storecredit.service import StoreCreditService
from app.shared.enums import (
    AcquisitionType,
    CashMovementType,
    ContactRole,
    Grade,
    ItemKind,
    OwnershipType,
    PayoutMethod,
    StockReason,
    StoreCreditSourceType,
)
from app.shared.exceptions import (
    AcquisitionRequiresNationalId,
    ContactNotFound,
    IdempotencyKeyConflict,
    InvalidCommissionPct,
    InvalidPayoutSplit,
    NoOpenCashSession,
    StoreCreditMemberRequired,
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
        self._settings = StoreSettingsService(session)
        self._storecredit = StoreCreditService(session)
        self._cash = CashDrawerService(session)

    async def get_acquisition(self, store_id: int, acquisition_id: int) -> Acquisition | None:
        return await self._repo.get(store_id, acquisition_id)

    @staticmethod
    def _fingerprint(data: AcquisitionCreate) -> str:
        """請求內容穩定 sha256（D-2 模式）：同 key 重送比對是否同一請求。"""
        canonical = json.dumps(data.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def find_idempotent_replay(
        self, store_id: int, idempotency_key: str, data: AcquisitionCreate
    ) -> AcquisitionResult | None:
        """同 key 已有收購單 → 內容相同回原結果（含識別碼重建）、不同 → 409。"""
        existing = await self._repo.get_by_idempotency_key(store_id, idempotency_key)
        if existing is None:
            return None
        if existing.idempotency_fingerprint != self._fingerprint(data):
            raise IdempotencyKeyConflict(
                f"idempotency key 已用於不同的收購內容（acquisition {existing.id}）"
            )
        item_codes, lot_code = await self._repo.get_codes(store_id, existing.id)
        return AcquisitionResult(
            acquisition_id=existing.id,
            type=existing.type,
            contact_id=existing.contact_id,
            total_cash_paid=existing.total_cash_paid,
            payout_method=existing.payout_method,
            payout_cash_amount=existing.payout_cash_amount,
            payout_credit_cash_equivalent=existing.payout_credit_cash_equivalent,
            item_codes=item_codes,
            lot_code=lot_code,
        )

    @staticmethod
    def _payout_total_from_request(data: AcquisitionCreate) -> Decimal:
        """自請求純算應付總額（零寫入）：供「任何副作用之前」的撥款預檢。

        與入庫實算同源（BUYOUT＝Σ acquisition_cost、BULK_LOT＝lot.acquisition_cost），
        兩者必然相等。
        """
        if data.type == AcquisitionType.BULK_LOT:
            assert data.lot is not None  # schema/_check_shape 已保證
            return Decimal(round_ntd(Decimal(data.lot.acquisition_cost)))
        total = sum(
            (Decimal(item.acquisition_cost or 0) for item in (data.items or [])), Decimal(0)
        )
        return Decimal(round_ntd(total))

    @staticmethod
    def _split_payout(data: AcquisitionCreate, total: Decimal) -> tuple[Decimal, Decimal]:
        """依撥款方式拆（現金部分, 購物金現金等值）；SPLIT 由現金部分推導購物金部分。

        SPLIT 的現金部分必須小於應付總額（等於＝CASH、超過＝多付），兩部分皆 >0
        （docs/16 §1.7）。
        """
        # service 邊界完整驗證（Codex：schema 可被 model_construct 繞過；
        # 負/零現金部分會造成「無現金腿」或購物金超發）。
        if data.payout_method is not PayoutMethod.SPLIT:
            if data.payout_split_cash is not None:
                raise InvalidPayoutSplit("僅 SPLIT 可提供 payout_split_cash")
            if data.payout_method is PayoutMethod.CASH:
                return total, Decimal(0)
            return Decimal(0), total
        if data.payout_split_cash is None:
            raise InvalidPayoutSplit("SPLIT 必須提供現金部分（payout_split_cash）")
        cash_part = Decimal(data.payout_split_cash)
        if cash_part != cash_part.to_integral_value():
            raise InvalidPayoutSplit("SPLIT 現金部分必須為整數元")
        if cash_part <= 0 or cash_part >= total:
            raise InvalidPayoutSplit(
                f"SPLIT 現金部分（{cash_part}）必須介於 0 與應付總額（{total}）之間（不含端點）"
            )
        return cash_part, total - cash_part

    async def create_acquisition(
        self,
        store_id: int,
        clerk_user_id: int,
        data: AcquisitionCreate,
        *,
        idempotency_key: str,
    ) -> AcquisitionResult:
        """建立收購單並完成入庫/付現。

        **service 邊界原子性（Codex 第六輪）**：主體包在 savepoint 內，任何例外
        （含未來新增的失敗模式：溢價政策、帳本漂移…）都自動回滾本操作的全部
        寫入——直呼 service、catch 例外後不回滾就 commit 的呼叫者，也不可能
        留下半套。外層交易由呼叫端 commit/rollback。
        """
        # 冪等為 service 邊界必填（Codex：任何呼叫者重試都不得重複付現/入購物金）。
        # 執行期守衛（第八輪）：型別註記擋不住 None/空字串——NULL 鍵不受唯一約束，
        # 並發首寫會重複撥款。
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise IdempotencyKeyConflict("idempotency_key 必須為非空字串")
        replay = await self.find_idempotent_replay(store_id, idempotency_key, data)
        if replay is not None:
            return replay
        try:
            async with self._session.begin_nested():
                return await self._create_acquisition_impl(
                    store_id, clerk_user_id, data, idempotency_key
                )
        except IntegrityError as exc:
            # 並行同 key（Codex 第七輪）：輸家撞唯一約束——savepoint 已回滾，
            # 在 service 層轉成「回原結果 / 409」，所有呼叫者同一語意。
            if "uq_acquisitions_store_idem_key" not in str(exc.orig):
                raise
            replay = await self.find_idempotent_replay(store_id, idempotency_key, data)
            if replay is None:
                raise IdempotencyKeyConflict("收購衝突，請重試") from exc
            return replay

    async def _create_acquisition_impl(
        self,
        store_id: int,
        clerk_user_id: int,
        data: AcquisitionCreate,
        idempotency_key: str,
    ) -> AcquisitionResult:
        contact = await self._contacts.get_contact(store_id, data.contact_id)
        if contact is None:
            raise ContactNotFound(f"找不到 contact {data.contact_id}")
        if contact.national_id_enc is None:
            raise AcquisitionRequiresNationalId("收購/寄售對象必須有 national_id")

        if data.type == AcquisitionType.CONSIGNMENT and (
            data.payout_method is not PayoutMethod.CASH or data.payout_split_cash is not None
        ):
            raise InvalidPayoutSplit("CONSIGNMENT 不撥款，不可指定撥款方式/拆分")
        pays_out = data.type in _CASH_PAYING
        # 純購物金不碰現金、不要求開帳（docs/16 §3.1）；含現金部分才要求。
        needs_cash_session = pays_out and data.payout_method in (
            PayoutMethod.CASH,
            PayoutMethod.SPLIT,
        )
        if needs_cash_session and await self._cash.get_current_session(store_id) is None:
            raise NoOpenCashSession("收購付現必須在開帳中的 cash_session 下進行，請先開帳")

        # 撥款預檢（Codex 第五輪 high）：在**任何寫入之前**完成全部驗證——
        # 直呼 service 又不回滾的呼叫者也不可能留下半套（入庫了卻沒撥款）。
        if pays_out:
            expected_total = self._payout_total_from_request(data)
            expected_cash, expected_credit = self._split_payout(data, expected_total)
            if expected_credit > 0 and ContactRole.MEMBER.value not in contact.roles:
                raise StoreCreditMemberRequired(
                    f"contact {contact.id} 非本店會員，不可持有購物金（I-8）"
                )

        acquisition = await self._repo.add(
            Acquisition(
                store_id=store_id,
                type=data.type,
                contact_id=contact.id,
                clerk_user_id=clerk_user_id,
                note=data.note,
                idempotency_key=idempotency_key,
                idempotency_fingerprint=self._fingerprint(data),
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

        if pays_out:
            total_payout = Decimal(round_ntd(total_cash))
            cash_part, credit_part = self._split_payout(data, total_payout)
            acquisition.payout_method = data.payout_method
            acquisition.payout_cash_amount = cash_part
            acquisition.payout_credit_cash_equivalent = credit_part
            acquisition.total_cash_paid = cash_part  # 語意維持「實際付現」
            if cash_part > 0:
                await self._cash.record_movement(
                    store_id,
                    CashMovementType.BUYOUT_OUT,
                    cash_part,
                    actor_user_id=clerk_user_id,
                    ref_type="acquisition",
                    ref_id=acquisition.id,
                )
            if credit_part > 0:
                # 同一原子交易：購物金入帳與收購同生共死（docs/16 §3.1）；
                # 溢價率取當下 settings（入帳列自帶三值可重現，I-4）。
                premium = Decimal(
                    (await self._settings.get_effective_settings(store_id)).premium_rate
                )
                await self._storecredit.credit(
                    store_id,
                    contact.id,
                    cash_equivalent=credit_part,
                    premium_rate=premium,
                    source_type=StoreCreditSourceType.ACQUISITION,
                    source_id=acquisition.id,
                    created_by=clerk_user_id,
                )
            await self._session.flush()

        # 收購入庫稽核（溯源）：只記參照與彙總，絕不含 national_id 等 PII 明文（§5）。
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=clerk_user_id,
            action="CREATE_ACQUISITION",
            entity_type="acquisition",
            entity_id=str(acquisition.id),
            after={
                "type": data.type.value,
                "contact_id": contact.id,
                "total_cash_paid": (
                    str(acquisition.total_cash_paid)
                    if acquisition.total_cash_paid is not None
                    else None
                ),
                "item_count": len(item_codes),
                "lot_code": lot_code,
            },
        )

        return AcquisitionResult(
            acquisition_id=acquisition.id,
            type=data.type,
            contact_id=contact.id,
            total_cash_paid=acquisition.total_cash_paid,
            payout_method=acquisition.payout_method,
            payout_cash_amount=acquisition.payout_cash_amount,
            payout_credit_cash_equivalent=acquisition.payout_credit_cash_equivalent,
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
