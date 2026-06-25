"""storecredit 業務邏輯：唯一寫入路徑＋不變量守衛（docs/16 §2、ADR-012）。

所有分錄都經 `_write_entry` 單一路徑：鎖帳戶列（D-1 模式）→ 算 balance_after
（>=0 否則 InsufficientStoreCredit）→ INSERT 帳本 → 更新快取（version+1）。
冪等（I-5，沿 D-2）：同 (store_id, source_type, source_id, entry_type) 重送，
指紋相同回原列、不同丟 StoreCreditConflict；並行重送由唯一約束擋下，呼叫端
（router/整合流程）據 IntegrityError 走 find_replay。

跨模組只經 service（§2）：收購（SC-2）/銷售（SC-3）呼叫本 service 的
credit/debit/reverse；本模組不碰他模組資料表。
"""

import hashlib
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.money import round_ntd
from app.modules.contacts.service import ContactService
from app.modules.reports.aging import BUCKET_KEYS, IssuedLot, age_outstanding
from app.modules.storecredit.models import StoreCreditLedger
from app.modules.storecredit.repository import StoreCreditRepository
from app.shared.enums import (
    ContactRole,
    StoreCreditEntryType,
    StoreCreditSourceType,
)
from app.shared.exceptions import (
    CrossStoreReference,
    InsufficientStoreCredit,
    StoreCreditConflict,
    StoreCreditMemberRequired,
)


def _fingerprint(
    *,
    store_id: int,
    contact_id: int,
    entry_type: StoreCreditEntryType,
    signed_amount: Decimal,
    source_type: StoreCreditSourceType,
    source_id: int | None,
    cash_equivalent: Decimal | None,
    premium_rate_applied: Decimal | None,
    reversal_of_id: int | None,
    idempotency_key: str | None,
) -> str:
    canonical = "|".join(
        str(part)
        for part in (
            store_id,
            contact_id,
            entry_type.value,
            signed_amount,
            source_type.value,
            source_id,
            cash_equivalent,
            premium_rate_applied,
            reversal_of_id,
            idempotency_key,
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# 溢價率政策界線（docs/16 §1.5 預設 0%–20%；SC-5 後改由 settings 提供 min/max，
# 此為 service 邊界的硬守衛——超界 CREDIT 會寫出「自洽但違反政策」的負債，
# I-3 對帳抓不到（adversarial 第八輪 medium））。
PREMIUM_RATE_MIN = Decimal("0.0000")
PREMIUM_RATE_MAX = Decimal("0.2000")


class StoreCreditService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = StoreCreditRepository(session)
        self._contacts = ContactService(session)

    # ── 寫入（唯一路徑）──

    async def _write_entry(
        self,
        store_id: int,
        contact_id: int,
        *,
        entry_type: StoreCreditEntryType,
        signed_amount: Decimal,
        source_type: StoreCreditSourceType,
        source_id: int | None,
        created_by: int,
        cash_equivalent: Decimal | None = None,
        premium_rate_applied: Decimal | None = None,
        reversal_of_id: int | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[StoreCreditLedger, bool]:
        """寫入一筆分錄；回 (分錄, 是否新插入)——冪等重放回 (原列, False)，
        呼叫端據此避免重複副作用（如稽核）。"""
        if signed_amount == 0:
            raise StoreCreditConflict("分錄金額不可為零")
        # 整數元守衛（§6；adversarial 第六輪 high）：Numeric(12,0) 會在持久化時
        # 各自捨入，非整數金額將使「SUM == balance_after == 快取」不變量破裂。
        if signed_amount != signed_amount.to_integral_value():
            raise StoreCreditConflict("分錄金額必須為整數元")
        if cash_equivalent is not None and cash_equivalent != cash_equivalent.to_integral_value():
            raise StoreCreditConflict("現金等值必須為整數元")
        # 多分店隔離（§4，adversarial review high）：contact 必須屬於本店——
        # 否則會建立「A 店 contact 配 B 店帳戶」的越界配對。所有寫入路徑統一在此守。
        if await self._contacts.get_contact(store_id, contact_id) is None:
            raise CrossStoreReference(f"contact {contact_id} 不屬於 store {store_id}")
        fingerprint = _fingerprint(
            store_id=store_id,
            contact_id=contact_id,
            entry_type=entry_type,
            signed_amount=signed_amount,
            source_type=source_type,
            source_id=source_id,
            cash_equivalent=cash_equivalent,
            premium_rate_applied=premium_rate_applied,
            reversal_of_id=reversal_of_id,
            idempotency_key=idempotency_key,
        )
        # 先鎖帳戶列（同帳戶寫入序列化），**鎖內**才做冪等/沖正重查（adversarial
        # 第二輪 high：鎖前 pre-check 在並發重試下兩邊都會通過，輸家撞唯一約束
        # 變成 500 而非約定的「回原列 / 409」）。
        account = await self._repo.lock_account(store_id, contact_id)
        replay = await self._find_replay_locked(
            store_id,
            entry_type=entry_type,
            source_type=source_type,
            source_id=source_id,
            reversal_of_id=reversal_of_id,
            fingerprint=fingerprint,
            idempotency_key=idempotency_key,
        )
        if replay is not None:
            return replay, False
        # 寫入前一致性檢查（adversarial 第十二輪 high）：快取若已漂移，繼續寫
        # 會把錯的 balance_after 燒進不可變帳本（事後對帳只能發現、不能修正）。
        # 鎖內重算帳本餘額為權威基底；三方不一致即硬中止、先對帳。
        ledger_balance = await self._repo.sum_signed(store_id, contact_id)
        latest_after = await self._repo.latest_balance_after(store_id, contact_id)
        cached = Decimal(account.balance)
        if cached != ledger_balance or (latest_after is not None and latest_after != cached):
            raise StoreCreditConflict(
                f"contact {contact_id} 帳本/快取不一致（帳本 {ledger_balance}、"
                f"快取 {cached}），寫入中止——請先執行對帳處理"
            )
        new_balance = ledger_balance + signed_amount
        if new_balance < 0:
            raise InsufficientStoreCredit(
                f"contact {contact_id} 購物金餘額不足（{account.balance} {signed_amount:+}）"
            )
        # savepoint 包插入：跨帳戶極端競態仍可能撞唯一約束（帳戶鎖只序列化同帳戶），
        # 撞到時交易未廢，重查一次轉成冪等回列或 409，不冒 IntegrityError 500。
        try:
            async with self._session.begin_nested():
                entry = await self._repo.insert_entry(
                    StoreCreditLedger(
                        store_id=store_id,
                        contact_id=contact_id,
                        entry_type=entry_type,
                        signed_amount=signed_amount,
                        balance_after=new_balance,
                        cash_equivalent=cash_equivalent,
                        premium_rate_applied=premium_rate_applied,
                        source_type=source_type,
                        source_id=source_id,
                        reversal_of_id=reversal_of_id,
                        fingerprint=fingerprint,
                        idempotency_key=idempotency_key,
                        reason=reason,
                        created_by=created_by,
                    )
                )
        except IntegrityError as exc:
            replay = await self._find_replay_locked(
                store_id,
                entry_type=entry_type,
                source_type=source_type,
                source_id=source_id,
                reversal_of_id=reversal_of_id,
                fingerprint=fingerprint,
                idempotency_key=idempotency_key,
            )
            if replay is not None:
                return replay, False
            raise StoreCreditConflict(f"分錄寫入衝突（{source_type}:{source_id}），請重試") from exc
        # 快取由 DB trigger（store_credit_cache_sync）原子推進；ORM 端 expire
        # 讓同交易後續讀取重抓最新值，service 不再手動改快取（單一事實來源）。
        await self._session.flush()
        self._session.expire(account)
        return entry, True

    async def _find_replay_locked(
        self,
        store_id: int,
        *,
        entry_type: StoreCreditEntryType,
        source_type: StoreCreditSourceType,
        source_id: int | None,
        reversal_of_id: int | None,
        fingerprint: str,
        idempotency_key: str | None,
    ) -> StoreCreditLedger | None:
        """鎖內冪等判定：同來源同指紋 → 回原列；同來源/同沖正對象但內容不同 → 409。"""
        if idempotency_key is not None:
            existing_key = await self._repo.find_by_idempotency_key(store_id, idempotency_key)
            if existing_key is not None:
                if existing_key.fingerprint == fingerprint:
                    return existing_key
                raise StoreCreditConflict(f"冪等鍵 {idempotency_key} 已用於不同內容的校正")
        if reversal_of_id is not None:
            existing_reversal = await self._repo.find_reversal_of(store_id, reversal_of_id)
            if existing_reversal is not None:
                if existing_reversal.fingerprint == fingerprint:
                    return existing_reversal
                raise StoreCreditConflict(
                    f"分錄 {reversal_of_id} 已被沖正（reversal {existing_reversal.id}），不可重複沖"
                )
        if source_id is not None:
            existing = await self._repo.find_by_source(store_id, source_type, source_id, entry_type)
            if existing is not None:
                if existing.fingerprint == fingerprint:
                    return existing
                raise StoreCreditConflict(
                    f"來源 {source_type}:{source_id} 已有 {entry_type} 分錄且內容不同"
                )
        return None

    async def _require_member(self, store_id: int, contact_id: int) -> None:
        # FOR UPDATE 鎖定 contact 列再驗（鎖序 contact→account）：與 contacts 的 MEMBER
        # 移除守衛在同一列互斥，關閉「移除 MEMBER ⇄ 並發首筆入帳」競態（Codex high）。
        contact = await self._contacts.get_contact_for_update(store_id, contact_id)
        if contact is None or ContactRole.MEMBER.value not in contact.roles:
            raise StoreCreditMemberRequired(
                f"contact {contact_id} 非本店會員，不可持有購物金（I-8）"
            )

    # ── 入帳/扣抵/沖正/校正 ──

    async def credit(
        self,
        store_id: int,
        contact_id: int,
        *,
        cash_equivalent: Decimal,
        premium_rate: Decimal,
        source_type: StoreCreditSourceType,
        source_id: int,
        created_by: int,
    ) -> StoreCreditLedger:
        """收購入帳（CREDIT）：實發 = round_ntd(現金等值 × (1+溢價率))（I-4）。"""
        if cash_equivalent <= 0:
            raise StoreCreditConflict("現金等值必須為正")
        if source_type is not StoreCreditSourceType.ACQUISITION:
            raise StoreCreditConflict("CREDIT 只能來自 ACQUISITION（docs/16 §3.1）")
        if not (PREMIUM_RATE_MIN <= premium_rate <= PREMIUM_RATE_MAX):
            raise StoreCreditConflict(
                f"溢價率 {premium_rate} 超出政策界線 [{PREMIUM_RATE_MIN}, {PREMIUM_RATE_MAX}]"
            )
        await self._require_member(store_id, contact_id)
        amount = Decimal(round_ntd(cash_equivalent * (Decimal(1) + premium_rate)))
        entry, _ = await self._write_entry(
            store_id,
            contact_id,
            entry_type=StoreCreditEntryType.CREDIT,
            signed_amount=amount,
            source_type=source_type,
            source_id=source_id,
            created_by=created_by,
            cash_equivalent=cash_equivalent,
            premium_rate_applied=premium_rate,
        )
        return entry

    async def debit(
        self,
        store_id: int,
        contact_id: int,
        *,
        amount: Decimal,
        source_type: StoreCreditSourceType,
        source_id: int,
        created_by: int,
    ) -> StoreCreditLedger:
        """消費扣抵（DEBIT，負向）；餘額不足 → InsufficientStoreCredit（I-6）。"""
        if amount <= 0:
            raise StoreCreditConflict("扣抵金額必須為正")
        if source_type is not StoreCreditSourceType.SALE:
            raise StoreCreditConflict("DEBIT 只能來自 SALE（docs/16 §3.2）")
        entry, _ = await self._write_entry(
            store_id,
            contact_id,
            entry_type=StoreCreditEntryType.DEBIT,
            signed_amount=-amount,
            source_type=source_type,
            source_id=source_id,
            created_by=created_by,
        )
        return entry

    async def reverse(
        self,
        store_id: int,
        original_entry_id: int,
        *,
        source_type: StoreCreditSourceType,
        source_id: int,
        created_by: int,
    ) -> StoreCreditLedger:
        """沖正：方向與被沖正列相反；**一列只能被沖一次**（部分唯一索引）。

        只收 id、交易內**重載持久列**（adversarial 第五輪 high：不信任呼叫端
        物件——偽造/過期的 signed_amount 會寫出錯額沖正並封死正確沖正）。
        被沖正列必須屬於本店；同一來源重試 → 冪等回原沖正列；不同來源再沖
        同一列 → StoreCreditConflict。扣回方向餘額不足 → InsufficientStoreCredit，
        由呼叫端依 docs/16 §3.3 擋下轉人工。
        """
        if source_type not in (
            StoreCreditSourceType.SALE_VOID,
            StoreCreditSourceType.ACQUISITION_ROLLBACK,
        ):
            raise StoreCreditConflict("REVERSAL 來源僅限 SALE_VOID / ACQUISITION_ROLLBACK")
        original = await self._repo.get_entry(store_id, original_entry_id)
        if original is None:
            raise CrossStoreReference(f"被沖正列 {original_entry_id} 不存在於 store {store_id}")
        if original.entry_type == StoreCreditEntryType.REVERSAL:
            raise StoreCreditConflict(f"分錄 {original.id} 本身是沖正列，不可再沖")
        # 沖正來源須對應原列業務事件（adversarial 第十三輪 high）：
        # SALE_VOID 只能沖銷售扣抵（DEBIT/SALE）、ACQUISITION_ROLLBACK 只能沖
        # 收購入帳（CREDIT/ACQUISITION）——錯配在算術上自洽、對帳抓不到。
        valid_pairs = {
            StoreCreditSourceType.SALE_VOID: (
                StoreCreditEntryType.DEBIT,
                StoreCreditSourceType.SALE,
            ),
            StoreCreditSourceType.ACQUISITION_ROLLBACK: (
                StoreCreditEntryType.CREDIT,
                StoreCreditSourceType.ACQUISITION,
            ),
        }
        expected_entry, expected_source = valid_pairs[source_type]
        if original.entry_type != expected_entry or original.source_type != expected_source:
            raise StoreCreditConflict(
                f"{source_type} 不可沖 {original.entry_type}/{original.source_type} 列"
            )
        # 沖正須追溯同一業務事件（第十四輪 high）：錯 id 的沖正會永久佔用該列
        # 唯一的沖正名額、封死真正的回滾/作廢。
        if source_id != original.source_id:
            raise StoreCreditConflict(
                f"沖正 source_id（{source_id}）必須等於原列 source_id（{original.source_id}）"
            )
        entry, _ = await self._write_entry(
            store_id,
            original.contact_id,
            entry_type=StoreCreditEntryType.REVERSAL,
            signed_amount=-Decimal(original.signed_amount),
            source_type=source_type,
            source_id=source_id,
            created_by=created_by,
            reversal_of_id=original.id,
        )
        return entry

    async def reverse_for_sale_void(
        self, store_id: int, sale_id: int, *, created_by: int
    ) -> StoreCreditLedger | None:
        """作廢銷售時沖回該筆的購物金扣抵（DEBIT/SALE → REVERSAL/SALE_VOID）。

        無購物金 tender（找不到對應 DEBIT）→ 回 None（不作為）。沖正本身冪等
        （同 source 重試回原沖正列），故重複作廢不會重複入回。
        """
        debit = await self._repo.find_by_source(
            store_id, StoreCreditSourceType.SALE, sale_id, StoreCreditEntryType.DEBIT
        )
        if debit is None:
            return None
        return await self.reverse(
            store_id,
            debit.id,
            source_type=StoreCreditSourceType.SALE_VOID,
            source_id=sale_id,
            created_by=created_by,
        )

    async def reverse_for_acquisition_void(
        self, store_id: int, acquisition_id: int, *, created_by: int
    ) -> StoreCreditLedger | None:
        """作廢收購時沖回該筆的購物金入帳（CREDIT/ACQUISITION → REVERSAL/ACQUISITION_ROLLBACK）。

        無購物金入帳（純現金收購，找不到 CREDIT）→ 回 None（不作為）。沖正冪等（同 source 重試
        回原沖正列），故重複作廢不會重複扣回。若會員已花用、沖回後餘額會 < 0 → reverse 內部丟
        InsufficientStoreCredit，由呼叫端擋作廢轉人工更正（F6.5：絕不允許負餘額）。
        """
        credit = await self._repo.find_by_source(
            store_id,
            StoreCreditSourceType.ACQUISITION,
            acquisition_id,
            StoreCreditEntryType.CREDIT,
        )
        if credit is None:
            return None
        return await self.reverse(
            store_id,
            credit.id,
            source_type=StoreCreditSourceType.ACQUISITION_ROLLBACK,
            source_id=acquisition_id,
            created_by=created_by,
        )

    async def adjust(
        self,
        store_id: int,
        contact_id: int,
        *,
        amount: Decimal,
        reason: str,
        created_by: int,
        idempotency_key: str,
    ) -> StoreCreditLedger:
        """人工校正（限 MANAGER——由 router 驗；必填事由；寫 audit，I-11）。

        MANUAL 無 source_id，重試防護走冪等鍵（adversarial 第三輪 high：
        雙擊/重送不得重複改負債）。
        """
        if not reason.strip():
            raise StoreCreditConflict("人工校正必須填寫事由")
        await self._require_member(store_id, contact_id)
        entry, inserted = await self._write_entry(
            store_id,
            contact_id,
            entry_type=StoreCreditEntryType.ADJUSTMENT,
            signed_amount=amount,
            source_type=StoreCreditSourceType.MANUAL,
            source_id=None,
            created_by=created_by,
            reason=reason.strip(),
            idempotency_key=idempotency_key,
        )
        if not inserted:
            return entry  # 冪等重放：不重複寫稽核（adversarial 第四輪 high）
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=created_by,
            action="STORE_CREDIT_ADJUST",
            entity_type="store_credit_account",
            entity_id=str(contact_id),
            before={"balance": str(Decimal(entry.balance_after) - amount)},
            after={"balance": str(entry.balance_after), "reason": reason.strip()},
            is_sensitive=True,
        )
        return entry

    # ── 查詢/對帳 ──

    async def get_balance(self, store_id: int, contact_id: int) -> Decimal:
        account = await self._repo.get_account(store_id, contact_id)
        return Decimal(account.balance) if account is not None else Decimal(0)

    async def has_store_credit(self, store_id: int, contact_id: int) -> bool:
        """是否已有購物金帳戶或任何帳本分錄（read-only）。

        供 contacts 判定能否移除 MEMBER 角色（I-8）：仍掛帳戶/帳本即不可移除，
        否則會留下非會員持有的購物金負債（Codex 對抗式審查 high）。
        """
        if await self._repo.get_account(store_id, contact_id) is not None:
            return True
        return bool(await self._repo.list_entries(store_id, contact_id, limit=1, offset=0))

    async def list_entries(
        self, store_id: int, contact_id: int, *, limit: int = 50, offset: int = 0
    ) -> list[StoreCreditLedger]:
        """帳戶異動歷史（分頁，新到舊）。"""
        return await self._repo.list_entries(store_id, contact_id, limit=limit, offset=offset)

    async def find_entry_by_source(
        self,
        store_id: int,
        source_type: StoreCreditSourceType,
        source_id: int,
        entry_type: StoreCreditEntryType,
    ) -> StoreCreditLedger | None:
        """供整合點（SC-2/3 沖正）找被沖正列。"""
        return await self._repo.find_by_source(store_id, source_type, source_id, entry_type)

    async def reconcile(self, store_id: int) -> dict[str, object]:
        """I-3 對帳：每帳戶 SUM(帳本) == 快取 == 最新 balance_after；回報不符清單。

        不符**只回報、不靜默修正**（docs/16 §2 I-3）。另回全域總負債（Σ 正餘額）。
        """
        mismatches: list[dict[str, str | int]] = []
        # 孤兒帳本偵測（adversarial 第六輪 high；DB FK 已擋新寫入，這裡涵蓋
        # 歷史/極端情況）：帳本出現過、卻無帳戶列的 contact 一律列為不符。
        ledger_contacts = await self._repo.list_ledger_contacts(store_id)
        account_contacts = {
            account.contact_id for account in await self._repo.list_accounts(store_id)
        }
        for contact_id in ledger_contacts:
            if contact_id not in account_contacts:
                mismatches.append(
                    {
                        "contact_id": contact_id,
                        "ledger_sum": str(await self._repo.sum_signed(store_id, contact_id)),
                        "cached": "（無帳戶列）",
                        "latest_balance_after": "",
                    }
                )
        # 全鏈驗證（第十四輪 medium）：每一列 balance_after 必等於該列止的滾動和
        # ——只驗最新列會漏掉「中段偽造、尾列補平」的歷史污染。
        for row_id in await self._repo.rows_violating_chain(store_id):
            mismatches.append(
                {
                    "contact_id": -1,
                    "ledger_sum": f"列 {row_id} 的 balance_after 不等於滾動和",
                    "cached": "",
                    "latest_balance_after": "",
                }
            )
        for account in await self._repo.list_accounts(store_id):
            ledger_sum = await self._repo.sum_signed(store_id, account.contact_id)
            latest = await self._repo.latest_balance_after(store_id, account.contact_id)
            cached = Decimal(account.balance)
            if ledger_sum != cached or (latest is not None and latest != cached):
                mismatches.append(
                    {
                        "contact_id": account.contact_id,
                        "ledger_sum": str(ledger_sum),
                        "cached": str(cached),
                        "latest_balance_after": "" if latest is None else str(latest),
                    }
                )
        # 總負債雙值（adversarial 第八輪 high）：快取值在有不符時不可信，
        # 一律同時回帳本推導值（含孤兒帳本）；呈報以 ledger 值為準。
        return {
            "store_id": store_id,
            "accounts_checked": len(await self._repo.list_accounts(store_id)),
            "mismatches": mismatches,
            "ledger_total_outstanding": str(await self._repo.ledger_total_outstanding(store_id)),
            "cached_total_outstanding": str(await self._repo.total_outstanding(store_id)),
            "cached_total_trustworthy": not mismatches,
        }

    # ── SC-4 報表資料（read-only；docs/16 §5A）──

    async def per_member_balances(self, store_id: int) -> list[tuple[int, Decimal]]:
        """各會員正餘額（balance > 0），供負債報表逐會員列示。"""
        balances = await self._repo.balances_by_contact(store_id)
        return [(cid, bal) for cid, bal in sorted(balances.items()) if bal > 0]

    async def balances_for(self, store_id: int, contact_ids: list[int]) -> dict[int, Decimal]:
        """指定會員的購物金餘額（無帳戶者視為 0）；供 contacts 會員清單批次取餘額、避免 N+1。"""
        if not contact_ids:
            return {}
        balances = await self._repo.balances_by_contact(store_id)
        return {cid: balances.get(cid, Decimal(0)) for cid in contact_ids}

    async def aging_report(self, store_id: int, *, now: datetime) -> dict[str, object]:
        """未兌付負債帳齡分桶（FIFO 沖銷發出列；docs/16 §5A）。"""
        lots_rows = await self._repo.positive_lots(store_id)
        positive_sum = await self._repo.positive_sum_by_contact(store_id)
        balances = await self._repo.balances_by_contact(store_id)
        per_contact: dict[int, list[IssuedLot]] = {}
        for contact_id, amount, issued_at in lots_rows:
            per_contact.setdefault(contact_id, []).append(
                IssuedLot(amount=amount, issued_at=issued_at)
            )
        buckets: OrderedDict[str, Decimal] = OrderedDict((k, Decimal(0)) for k in BUCKET_KEYS)
        for contact_id, lots in per_contact.items():
            balance = balances.get(contact_id, Decimal(0))
            if balance <= 0:
                continue  # 無未兌付餘額者不入帳齡
            consumed = positive_sum.get(contact_id, Decimal(0)) - balance
            contact_buckets = age_outstanding(lots, consumed, now)
            for key, value in contact_buckets.items():
                buckets[key] += value
        return {
            "total_outstanding": await self._repo.total_outstanding(store_id),
            "buckets": buckets,
        }

    async def flows(
        self,
        store_id: int,
        *,
        date_from: datetime,
        date_to: datetime,
        granularity: str,
    ) -> list[dict[str, object]]:
        """期間發出/兌付/淨變化彙總（granularity=day/week/month；docs/16 §5A）。

        每期回毛額/沖正/淨額分欄（docs/19 §3.2 稽核分欄）：
        issued = issued_gross - issued_reversed、redeemed = redeemed_gross - redeemed_reversed、
        net_change = issued - redeemed + adjustment_net。issued/redeemed 為 net 欄；
        net_change 納入人工 ADJUSTMENT，恰等於該期帳本 signed 淨變化、可與 liability 差額對上
        （docs/19 §3.1）。
        """
        if granularity not in ("day", "week", "month"):
            raise StoreCreditConflict("granularity 僅支援 day/week/month")
        rows = await self._repo.flows(
            store_id,
            date_from=date_from,
            date_to=date_to,
            granularity=granularity,
        )
        result: list[dict[str, object]] = []
        for period, issued_gross, issued_reversed, redeemed_gross, redeemed_reversed, adj in rows:
            issued = issued_gross - issued_reversed
            redeemed = redeemed_gross - redeemed_reversed
            result.append(
                {
                    "period": period,
                    "issued": issued,
                    "redeemed": redeemed,
                    "net_change": issued - redeemed + adj,
                    "issued_gross": issued_gross,
                    "issued_reversed": issued_reversed,
                    "redeemed_gross": redeemed_gross,
                    "redeemed_reversed": redeemed_reversed,
                    "adjustment_net": adj,
                }
            )
        return result
