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
from datetime import UTC, datetime
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
    StoreCreditEntryType,
    StoreCreditSourceType,
)
from app.shared.exceptions import (
    AcquisitionAlreadyVoid,
    AcquisitionCreditSpent,
    AcquisitionHasSoldItems,
    AcquisitionNotFound,
    AcquisitionRequiresNationalId,
    AcquisitionVoidUnsupported,
    ContactNotFound,
    IdempotencyKeyConflict,
    InsufficientStoreCredit,
    InvalidAcquisitionCategory,
    InvalidCommissionPct,
    InvalidPayoutSplit,
    NoOpenCashSession,
    SignatureContentMismatch,
    SignatureTaskConflict,
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

    async def find_by_signature_task(
        self, store_id: int, signature_task_id: int
    ) -> Acquisition | None:
        """以切結任務反查綁定的收購單（簽署證據調閱用，read-only）。"""
        return await self._repo.get_by_signature_task_id(
            store_id, signature_task_id, for_update=False
        )

    async def get_acquisition(self, store_id: int, acquisition_id: int) -> Acquisition | None:
        return await self._repo.get(store_id, acquisition_id)

    async def list_by_contact(
        self, store_id: int, contact_id: int, *, limit: int = 50, offset: int = 0
    ) -> list[Acquisition]:
        """某會員帶來的收購單（買斷/寄售來源；會員中心；store 範圍、分頁；docs/17 §5.2）。"""
        return await self._repo.list_by_contact(store_id, contact_id, limit=limit, offset=offset)

    async def list_ids_by_contact(self, store_id: int, contact_id: int) -> list[int]:
        """某會員的所有收購單 id（供 sourced-items 反查買斷庫存；id-only；docs/17 §5.2）。"""
        return await self._repo.list_ids_by_contact(store_id, contact_id)

    async def count_payouts_by_method(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> dict[PayoutMethod, int]:
        """期間內各撥款方式的收購筆數（供 SC-5b 報表/引擎計 take_rate；唯讀，§2 經 service）。"""
        return await self._repo.count_payouts_by_method(store_id, date_from, date_to)

    @staticmethod
    def _fingerprint(data: AcquisitionCreate) -> str:
        """請求內容穩定 sha256（D-2 模式）：同 key 重送比對是否同一請求。"""
        canonical = json.dumps(data.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def find_idempotent_replay(
        self, store_id: int, idempotency_key: str, data: AcquisitionCreate
    ) -> AcquisitionResult | None:
        """同 key 已有收購單 → 內容相同回原結果（含識別碼重建）、不同 → 409。"""
        # 鎖列讀（第十五輪）：回放決策須與並行 void 序列化，不得讀到 void 前舊版而誤回成功。
        existing = await self._repo.get_by_idempotency_key(
            store_id, idempotency_key, for_update=True
        )
        if existing is None:
            return None
        if existing.idempotency_fingerprint != self._fingerprint(data):
            raise IdempotencyKeyConflict(
                f"idempotency key 已用於不同的收購內容（acquisition {existing.id}）"
            )
        # 已作廢收購不可以原冪等鍵重放為成功（Codex K4 第十四/十六輪 high）：前端凍結冪等鍵跨
        # 模糊 LAN 失敗重試，若回已作廢列會又開櫃/印標、與已反轉帳本脫節。**含未帶切結的一般
        # 現金收購**（切結政策預設關，此為主要路徑）——一律 409，不因是否簽署而異。
        if existing.voided_at is not None:
            if existing.signature_task_id is not None:
                raise SignatureTaskConflict(
                    "此切結任務綁定的收購已作廢，不可重放或重用；請重新推送簽署"
                )
            raise IdempotencyKeyConflict("此收購已作廢，不可以原冪等鍵重放；請重新建立收購")
        return await self._result_from_existing(store_id, existing)

    async def _result_from_existing(
        self, store_id: int, existing: Acquisition
    ) -> AcquisitionResult:
        """自既有收購單重建對外結果（識別碼補齊）——供冪等/切結重放共用。"""
        item_codes, lot_code = await self._repo.get_codes(store_id, existing.id)
        # 重放也要帶撥入購物金的帳本事實（與首發回應同值）：自帳本以來源反查本筆 CREDIT
        # 分錄（不可變），非另查活餘額。
        credit_granted: Decimal | None = None
        credit_balance_after: Decimal | None = None
        entry = await self._storecredit.find_entry_by_source(
            store_id,
            StoreCreditSourceType.ACQUISITION,
            existing.id,
            StoreCreditEntryType.CREDIT,
        )
        if entry is not None:
            credit_granted = Decimal(entry.signed_amount)
            credit_balance_after = Decimal(entry.balance_after)
        return AcquisitionResult(
            acquisition_id=existing.id,
            type=existing.type,
            contact_id=existing.contact_id,
            total_cash_paid=existing.total_cash_paid,
            payout_method=existing.payout_method,
            payout_cash_amount=existing.payout_cash_amount,
            payout_credit_cash_equivalent=existing.payout_credit_cash_equivalent,
            payout_credit_granted=credit_granted,
            payout_credit_balance_after=credit_balance_after,
            item_codes=item_codes,
            lot_code=lot_code,
        )

    async def _replay_by_signature_task(
        self, store_id: int, signature_task_id: int, data: AcquisitionCreate
    ) -> AcquisitionResult | None:
        """撞單次使用唯一約束時的「回應遺失重試」救援（Codex K4 第九輪）。

        前端每次 POST 產新 idempotency_key，故首次已 commit、回應在 LAN 途中遺失時，重試無法
        以冪等鍵重放，只會撞 `uq_acquisitions_signature_task`。若既有那張收購的**請求指紋**與
        本次相同（同一張收購、同一批品項金額撥款），回原結果讓前端跑成功路徑（取得單號/標籤、
        開抽屜），不重複撥款；指紋不同才是「拿別人的切結硬綁」→ 維持 409。
        """
        existing = await self._repo.get_by_signature_task_id(
            store_id, signature_task_id, for_update=True
        )
        if existing is None or existing.idempotency_fingerprint != self._fingerprint(data):
            return None
        # 已作廢者不可重放（第十三輪）：回 None 讓呼叫端維持 409，不把已反轉的收購當成功回應。
        if existing.voided_at is not None:
            return None
        return await self._result_from_existing(store_id, existing)

    @staticmethod
    def _payout_total_from_request(data: AcquisitionCreate) -> Decimal:
        """自請求純算應付總額（零寫入）：供「任何副作用之前」的撥款預檢。

        與入庫實算同源（BUYOUT＝Σ acquisition_cost、BULK_LOT＝lot.acquisition_cost），
        兩者必然相等。
        """
        if data.type == AcquisitionType.BULK_LOT:
            assert data.lot is not None  # schema/_check_shape 已保證
            lot_cost = Decimal(data.lot.acquisition_cost)
            if lot_cost < 0:
                raise InvalidPayoutSplit("收購成本不可為負（schema 繞過防護）")
            return Decimal(round_ntd(lot_cost))
        total = Decimal(0)
        for item in data.items or []:
            cost = Decimal(item.acquisition_cost or 0)
            if cost < 0:
                # 第十輪：model_construct 帶負成本會持久化「負撥款腿」且無任何
                # 現金/帳本副作用——在純算階段（零寫入）即拒。
                raise InvalidPayoutSplit("收購成本不可為負（schema 繞過防護）")
            total += cost
        return Decimal(round_ntd(total))

    @staticmethod
    def _signed_premium_rate(content: dict[str, object]) -> Decimal | None:
        """自切結快照的 store_credit_premium 取凍結溢價率（Decimal，[0, 政策硬上限]），
        無/非法 → None（Codex K4 第五輪：以簽署當下費率入帳，不重讀 settings）。"""
        prem = content.get("store_credit_premium")
        if not isinstance(prem, dict):
            return None
        try:
            rate = Decimal(str(prem.get("rate", "")))
        except (ValueError, ArithmeticError):
            return None
        if rate < 0 or rate > Decimal("0.20"):
            return None
        return rate

    @staticmethod
    def _whole_ntd(value: object) -> Decimal | None:
        """把切結快照的金額欄嚴格解析為非負整數元 Decimal；缺值/小數/負值/非數 → None
        （Codex K4 第三輪 high：不可截斷/預設，簽的錢必須就是入帳的錢）。"""
        if value is None:
            return None
        try:
            d = Decimal(str(value))
        except (ValueError, ArithmeticError):
            return None
        if d < 0 or d != d.to_integral_value():
            return None
        return d

    @staticmethod
    def _affidavit_content_matches(
        content: dict[str, object], data: AcquisitionCreate, expected_total: Decimal
    ) -> bool:
        """收購的品項名＋金額＋總額是否與切結內容快照**精確**相符（Codex K4 high）。

        金額以嚴格「非負整數元」語意解析（缺 name/amount、小數、負值一律不符）並以 Decimal
        精確比對；前端推送時以相同 draft 組出 content，故內容未改即相符、改了即不符。
        """

        def norm_items(raw: object) -> list[tuple[str, Decimal]] | None:
            if not isinstance(raw, list):
                return None
            out: list[tuple[str, Decimal]] = []
            for it in raw:
                if not isinstance(it, dict) or "name" not in it or "amount" not in it:
                    return None
                amt = AcquisitionService._whole_ntd(it.get("amount"))
                if amt is None:
                    return None
                out.append((str(it["name"]), amt))
            return sorted(out)

        signed_items = norm_items(content.get("items"))
        signed_total = AcquisitionService._whole_ntd(content.get("total"))
        if signed_items is None or signed_total is None:
            return False
        if data.type == AcquisitionType.BULK_LOT:
            assert data.lot is not None
            expected_items = [(data.lot.name or "散裝批", expected_total)]
            # 散裝批另須綁**數量與計價基準**（Codex K4 第十一輪 high）：否則客人簽後仍可改
            # total_qty/basis，建出客人未確認數量的存貨、破壞簽署快照。缺欄即視為不符。
            signed_lot = content.get("lot")
            if not isinstance(signed_lot, dict):
                return False
            signed_qty = signed_lot.get("total_qty")
            if not isinstance(signed_qty, int) or isinstance(signed_qty, bool):
                return False
            if signed_qty != data.lot.total_qty:
                return False
            signed_basis = str(signed_lot.get("acquisition_basis", ""))
            if signed_basis != str(data.lot.acquisition_basis.value):
                return False
        else:
            # 非散裝收購不得綁「含 lot 敘述」的切結（Codex K5 第九輪 high）：客人簽了散裝
            # 件數/基準，綁到 BUYOUT 時這些簽名事實不會被驗證——fail closed、要求重推重簽。
            if content.get("lot") is not None:
                return False
            expected_items = [
                ((it.name or "品項"), Decimal(str(it.acquisition_cost or 0)))
                for it in (data.items or [])
            ]
        return signed_items == sorted(expected_items) and signed_total == expected_total

    @staticmethod
    def _normalized_payout_method(data: AcquisitionCreate) -> PayoutMethod:
        """入口正規化（Codex 第九輪）：model_construct 可帶 raw string，StrEnum
        身分比較（is）會誤判；統一轉枚舉、非法值如實拒。"""
        try:
            return PayoutMethod(data.payout_method)
        except ValueError as exc:
            raise InvalidPayoutSplit(f"未知的撥款方式：{data.payout_method!r}") from exc

    @staticmethod
    def _split_payout(data: AcquisitionCreate, total: Decimal) -> tuple[Decimal, Decimal]:
        """依撥款方式拆（現金部分, 購物金現金等值）；SPLIT 由現金部分推導購物金部分。

        SPLIT 的現金部分必須小於應付總額（等於＝CASH、超過＝多付），兩部分皆 >0
        （docs/16 §1.7）。
        """
        # service 邊界完整驗證（Codex：schema 可被 model_construct 繞過；
        # 負/零現金部分會造成「無現金腿」或購物金超發）。
        method = AcquisitionService._normalized_payout_method(data)
        if method != PayoutMethod.SPLIT:
            if data.payout_split_cash is not None:
                raise InvalidPayoutSplit("僅 SPLIT 可提供 payout_split_cash")
            if method == PayoutMethod.CASH:
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
        # 切結收購「回應遺失重試」的**前置**重放（Codex K4 第十輪）：前端每次產新冪等鍵，故
        # 首次已 commit、回應在 LAN 途中遺失時無法以冪等鍵重放。若不在此**先於**任何當前狀態
        # 檢查（開帳/身分…）攔截，重試會在插入前先撞 NoOpenCashSession（抽屜已關）等而永遠
        # 走不到唯一約束、無從重放——收購/撥款已發生卻回不了成功路徑。故在此以 signature_task_id
        # 查既有收購：指紋相符回原結果重放；不符即該切結已綁別張 → 409。並發首寫競態仍由下方
        # IntegrityError 兜底（兩者都通過前置檢查、插入時才互撞）。
        if data.signature_task_id is not None:
            existing = await self._repo.get_by_signature_task_id(
                store_id, data.signature_task_id, for_update=True
            )
            if existing is not None:
                # 已作廢的收購不可重放（Codex K4 第十三輪 high）：回應遺失後店員可能已作廢該筆
                # （對稱反轉現金/庫存），若仍回 201 會讓前端又開櫃/印標、與已反轉的帳本脫節。
                # 該切結已被一筆（現已作廢）收購消耗＝單次使用已用掉 → 一律 409，需重新推送簽署。
                if existing.voided_at is not None:
                    raise SignatureTaskConflict(
                        "此切結任務綁定的收購已作廢，不可重放或重用；請重新推送簽署"
                    )
                if existing.idempotency_fingerprint == self._fingerprint(data):
                    return await self._result_from_existing(store_id, existing)
                raise SignatureTaskConflict("此切結任務已綁定另一張收購單，不可重複使用")
        try:
            async with self._session.begin_nested():
                return await self._create_acquisition_impl(
                    store_id, clerk_user_id, data, idempotency_key
                )
        except IntegrityError as exc:
            # 並發首寫競態（前置重放時尚無既有列，兩請求同時插入 → 輸家撞單次使用唯一約束）：
            # savepoint 已回滾，此時贏家已可見——指紋相符回原結果重放、不符維持 409（第九/十輪）。
            if "uq_acquisitions_signature_task" in str(exc.orig):
                if data.signature_task_id is not None:
                    replay = await self._replay_by_signature_task(
                        store_id, data.signature_task_id, data
                    )
                    if replay is not None:
                        return replay
                raise SignatureTaskConflict("此切結任務已綁定另一張收購單，不可重複使用") from exc
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

        # F6 additive 持久化：category_id 須屬本店（跨店租戶守衛）。純讀檢查、先於任何寫入。
        await self._validate_categories(store_id, data)

        payout_method = self._normalized_payout_method(data)
        if data.type == AcquisitionType.CONSIGNMENT and (
            payout_method != PayoutMethod.CASH or data.payout_split_cash is not None
        ):
            raise InvalidPayoutSplit("CONSIGNMENT 不撥款，不可指定撥款方式/拆分")
        pays_out = data.type in _CASH_PAYING

        # 手持切結綁定（docs/23 K4，D2）：帶 signature_task_id 時，驗證其為本店本會員之已簽
        # 收購切結（AFFIDAVIT/SIGNED），撥款與客人手持端所選一致（D7），且**收購的品項/金額/
        # 總額須與切結內容快照相符**——客人簽的必須就是這張收購，杜絕「簽了低價、事後改高價
        # 再沿用同一簽署」（Codex K4 high）。純讀驗證，先於任何寫入；單次使用由 DB UNIQUE 守護。
        # 簽署凍結的購物金溢價率（Codex K4 第五輪）：STORE_CREDIT 簽署收購以此入帳、不重讀。
        signed_premium_rate: Decimal | None = None

        # D2 政策（docs/23）：店家開啟 require_acquisition_affidavit 後，付現/購物金收購必須
        # 綁定已簽手持切結；未帶即擋（防「跳過簽署直接完成」的漏證據路徑，Codex K4 第四輪）。
        if pays_out and data.signature_task_id is None:
            require = (
                await self._settings.get_effective_settings(store_id)
            ).require_acquisition_affidavit
            if require:
                raise SignatureContentMismatch(
                    "本店已要求收購須手持切結：請先送至手持裝置由客人簽署後再完成收購"
                )

        if data.signature_task_id is not None:
            # 手持切結（AFFIDAVIT＋chosen_payout）僅適用付現/購物金的收購（BUYOUT/BULK_LOT）。
            # 寄售（CONSIGNMENT）不即時撥款、內容形態不同——絕不可把買斷切結綁到寄售上、
            # 燒掉該簽署又不驗內容（Codex K4 第二輪 high）。寄售一律拒收 signature_task_id。
            if not pays_out:
                raise SignatureContentMismatch(
                    "寄售（CONSIGNMENT）不支援手持切結綁定；請以買斷/散裝流程簽署"
                )
            from app.modules.signing.service import SigningService

            affidavit = await SigningService(self._session).get_signed_affidavit(
                store_id, data.signature_task_id, contact_id=contact.id
            )
            # 缺客人撥款選擇的切結不得用於收購（Codex K4 第三輪 high）：正常簽署一定有
            # chosen_payout（sign_task 強制二選一），但 legacy/匯入的壞列可能為 NULL。
            if affidavit.chosen_payout is None:
                raise SignatureContentMismatch(
                    "已簽切結缺少客人撥款選擇，不可用於收購，請重新推送簽署"
                )
            if affidavit.chosen_payout != payout_method:
                raise InvalidPayoutSplit(
                    f"收購撥款（{payout_method.value}）與已簽切結所選"
                    f"（{affidavit.chosen_payout.value}）不一致"
                )
            if not self._affidavit_content_matches(
                affidavit.content, data, self._payout_total_from_request(data)
            ):
                raise SignatureContentMismatch(
                    "收購內容（品項/金額）與已簽切結不符，請重新推送簽署"
                )
            # 身分一致（Codex K4 第六/七/八輪 high）：以**穩定指紋**（national_id_blind_index，
            # HMAC）而非有損顯示遮罩比對——遮罩有損（不同證號可同遮罩，如 A123456789 與
            # A120002789 皆 A12****789），只比遮罩會漏掉「同遮罩換證號」。且以 **FOR UPDATE 鎖定
            # 會員列**再比對並持鎖至 commit：與 contacts 的 national_id 編輯（同以行鎖）序列化，
            # 杜絕「比對後、commit 前被並發改證號」使收購綁到已失真身分（第八輪）。
            locked_contact = await self._contacts.get_contact_for_update(store_id, contact.id)
            if locked_contact is None:
                raise ContactNotFound(f"找不到 contact {contact.id}")
            # 指紋取自伺服器內部欄 identity_fingerprint（非 content——不外洩至手持端，K4 第十一輪）。
            signed_fp = affidavit.identity_fingerprint
            if not signed_fp or signed_fp != locked_contact.national_id_blind_index:
                raise SignatureContentMismatch("收購對象身分（證號）與已簽切結不符，請重新推送簽署")
            # 凍結簽署當下的購物金溢價率（Codex K4 第五輪 high）：客人看到並簽的溢價就是入帳的
            # 溢價——不可於簽署後因店長改 premium_rate 而以不同費率入帳，造成帳本/證據不符。
            if payout_method == PayoutMethod.STORE_CREDIT:
                signed_premium_rate = self._signed_premium_rate(affidavit.content)
                if signed_premium_rate is None:
                    raise SignatureContentMismatch(
                        "已簽切結缺少購物金溢價快照，不可入帳，請重新推送簽署"
                    )

        # 撥款預檢（Codex 第五輪 high）：在**任何寫入之前**完成全部驗證——
        # 直呼 service 又不回滾的呼叫者也不可能留下半套（入庫了卻沒撥款）。
        # 純輸入驗證先於開帳等狀態檢查：無對價的請求不論開帳與否一律 422。
        expected_cash = expected_credit = Decimal(0)
        if pays_out:
            expected_total = self._payout_total_from_request(data)
            # 零應付總額（第十三/十五輪）：付費型收購必須有對價——零元 CASH 會
            # 留下「入庫卻無任何撥款副作用」的單，與漏付路徑無法區分。若未來
            # 需要「受贈/免費入庫」，應另立明確流程，不借道收購。
            if expected_total <= 0:
                raise InvalidPayoutSplit(
                    "應付總額必須大於 0（BUYOUT/BULK_LOT 必須有對價；免費入庫請走獨立流程）"
                )
            expected_cash, expected_credit = self._split_payout(data, expected_total)
            if expected_credit > 0 and ContactRole.MEMBER.value not in contact.roles:
                raise StoreCreditMemberRequired(
                    f"contact {contact.id} 非本店會員，不可持有購物金（I-8）"
                )

        # 純購物金不碰現金、不要求開帳（docs/16 §3.1）；含現金部分才要求。
        needs_cash_session = pays_out and payout_method in (
            PayoutMethod.CASH,
            PayoutMethod.SPLIT,
        )
        if needs_cash_session and await self._cash.get_current_session(store_id) is None:
            raise NoOpenCashSession("收購付現必須在開帳中的 cash_session 下進行，請先開帳")

        # 撥款欄於建單時即帶上（第十四輪：DB 形狀 CHECK 嚴格化後，header 不可
        # 以「無撥款」形先落地再補）——預檢值與入庫實算同源必相等。
        acquisition = await self._repo.add(
            Acquisition(
                store_id=store_id,
                type=data.type,
                contact_id=contact.id,
                clerk_user_id=clerk_user_id,
                note=data.note,
                idempotency_key=idempotency_key,
                idempotency_fingerprint=self._fingerprint(data),
                signature_task_id=data.signature_task_id,
                payout_method=payout_method if pays_out else PayoutMethod.CASH,
                payout_cash_amount=expected_cash if pays_out else None,
                payout_credit_cash_equivalent=expected_credit if pays_out else None,
                total_cash_paid=expected_cash if pays_out else None,
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

        credit_granted: Decimal | None = None
        credit_balance_after: Decimal | None = None
        if pays_out:
            total_payout = Decimal(round_ntd(total_cash))
            cash_part, credit_part = self._split_payout(data, total_payout)
            # 入庫實算必須等於建單時的預檢值（同源；不等即程式錯誤，立即失敗）
            assert (cash_part, credit_part) == (expected_cash, expected_credit)
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
                # 同一原子交易：購物金入帳與收購同生共死（docs/16 §3.1）；入帳列自帶三值可重現
                # （I-4）。**已簽 STORE_CREDIT 收購以簽署凍結的溢價率入帳**（Codex K4 第五輪）——
                # 客人看到並簽的溢價＝入帳的溢價；非手持流程才取當下 settings。
                premium = (
                    signed_premium_rate
                    if signed_premium_rate is not None
                    else Decimal(
                        (await self._settings.get_effective_settings(store_id)).premium_rate
                    )
                )
                entry = await self._storecredit.credit(
                    store_id,
                    contact.id,
                    cash_equivalent=credit_part,
                    premium_rate=premium,
                    source_type=StoreCreditSourceType.ACQUISITION,
                    source_id=acquisition.id,
                    created_by=clerk_user_id,
                )
                # 憑證聯要印的帳本事實（2026-07-11 裁示）：實發（含溢價）與本筆分錄的
                # balance_after——取自剛寫入的不可變分錄，非另查會漂移的活餘額。
                credit_granted = Decimal(entry.signed_amount)
                credit_balance_after = Decimal(entry.balance_after)
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
            payout_credit_granted=credit_granted,
            payout_credit_balance_after=credit_balance_after,
            item_codes=item_codes,
            lot_code=lot_code,
        )

    async def void_acquisition(
        self, store_id: int, acquisition_id: int, *, actor_user_id: int, reason: str
    ) -> Acquisition:
        """作廢收購（manager，F6.5）：對稱反轉庫存/現金/購物金，全程稽核；單交易整筆成立/回滾。

        擋下（任一成立即拒，皆先於任何寫入）：已作廢、含已售庫存、付現但無開帳、購物金已花用沖回會負。
        併發：FOR UPDATE 鎖收購列＋刷新已提交——兩並行作廢只一成功（另一見已作廢）、稽核一筆。
        庫存以原子條件式轉移為後盾、購物金沖正餘額不足即整筆回滾。
        """
        acquisition = await self._repo.lock(store_id, acquisition_id)
        if acquisition is None:
            raise AcquisitionNotFound(f"找不到收購 {acquisition_id}")
        if acquisition.type == AcquisitionType.CONSIGNMENT:
            # 寄售品仍屬寄售人，作廢須走寄售退貨＋結算反轉（invariant #7），與買斷對稱反轉不同；
            # 寄售庫存為 CONSIGNMENT 持有、不在本作廢的 OWNED 讀層，故一律擋下（另立任務）。
            raise AcquisitionVoidUnsupported(
                f"收購 {acquisition_id} 為寄售類型，不支援作廢；請走寄售退貨/結算反轉流程"
            )
        if acquisition.voided_at is not None:
            raise AcquisitionAlreadyVoid(f"收購 {acquisition_id} 已作廢，不可重複作廢")

        # 讀層前置擋（清楚錯誤、早於任何寫入）：含已售庫存 → 不可作廢
        if await self._inventory.has_sold_items(store_id, acquisition_id):
            raise AcquisitionHasSoldItems(f"收購 {acquisition_id} 含已售出庫存，無法作廢")
        cash_back = acquisition.payout_cash_amount or Decimal(0)
        credit_back = acquisition.payout_credit_cash_equivalent or Decimal(0)
        # 付現的退款須落當前開帳 session（現行紅字，不改歷史）；無開帳 → 擋
        if cash_back > 0 and await self._cash.get_current_session(store_id) is None:
            raise NoOpenCashSession("作廢付現收購的退款需在開帳中的 cash_session 下進行，請先開帳")

        # 寫入（單一交易，router commit/rollback）。
        # 鎖序與 sales 一致：先鎖/轉移庫存列，再記現金/沖購物金。sale 為先鎖品列再鎖收銀，
        # 本作廢若反序會與並行銷售互卡 → DB 死結 500（Codex 高風險），故庫存退場置前。
        await self._inventory.void_acquisition_inventory(store_id, acquisition_id)
        if cash_back > 0:
            await self._cash.record_movement(
                store_id,
                CashMovementType.ACQUISITION_VOID_IN,
                cash_back,
                actor_user_id=actor_user_id,
                ref_type="acquisition_void",
                ref_id=acquisition_id,
            )
        if credit_back > 0:
            try:
                await self._storecredit.reverse_for_acquisition_void(
                    store_id, acquisition_id, created_by=actor_user_id
                )
            except InsufficientStoreCredit as exc:
                raise AcquisitionCreditSpent(
                    f"收購 {acquisition_id} 的購物金已被花用，無法作廢，請改用人工更正"
                ) from exc

        acquisition.voided_at = datetime.now(UTC)
        acquisition.voided_by = actor_user_id
        acquisition.void_reason = reason.strip()
        await self._session.flush()
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="VOID_ACQUISITION",
            entity_type="acquisition",
            entity_id=str(acquisition_id),
            before={"voided_at": None},
            after={
                # 不寫 free-form reason：可能含 PII（賣方姓名/電話/身分證），稽核必須無 PII（§5）。
                # 作廢原因保存在 acquisitions.void_reason 欄（其設計歸屬），不複製進不可變稽核。
                "voided_at": acquisition.voided_at.isoformat(),
                "reversed_cash": str(cash_back),
                "reversed_credit": str(credit_back),
            },
            is_sensitive=True,
        )
        return acquisition

    async def _validate_categories(self, store_id: int, data: AcquisitionCreate) -> None:
        """檢查所有帶入的 category_id 屬本店（不屬→422）；FK 不分店，須在 service 守。"""
        category_ids = {item.category_id for item in (data.items or []) if item.category_id}
        if data.lot is not None and data.lot.category_id is not None:
            category_ids.add(data.lot.category_id)
        for category_id in category_ids:
            if await self._inventory.get_category(store_id, category_id) is None:
                raise InvalidAcquisitionCategory(f"分類 {category_id} 不屬於 store {store_id}")

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
                category_id=item.category_id,
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
            category_id=lot.category_id,
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
