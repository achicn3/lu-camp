"""storecredit 資料存取層（唯一直接碰 ORM 的層）。

帳本只提供 INSERT（I-1）；帳戶列以 SELECT … FOR UPDATE 取得（寫入序列化錨點，
沿 D-1 模式）。餘額重算（SUM）供 I-3 對帳。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import ColumnElement, and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.time import STORE_TIME_ZONE_NAME
from app.modules.storecredit.models import (
    StoreCreditAccount,
    StoreCreditLedger,
    StoreCreditSuggestionLog,
)
from app.shared.enums import StoreCreditEntryType, StoreCreditSourceType


class StoreCreditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_account(self, store_id: int, contact_id: int) -> StoreCreditAccount:
        """取得帳戶列並上 row lock；不存在則建立後再鎖（首寫情境）。"""
        stmt = (
            select(StoreCreditAccount)
            .where(
                StoreCreditAccount.store_id == store_id,
                StoreCreditAccount.contact_id == contact_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        account: StoreCreditAccount | None = await self._session.scalar(stmt)
        if account is None:
            # 並發首寫可能撞唯一約束：以 ON CONFLICT DO NOTHING 容忍後重查上鎖。
            await self._session.execute(
                text(
                    "INSERT INTO store_credit_accounts"
                    " (store_id, contact_id, balance, version, created_at, updated_at)"
                    " VALUES (:store_id, :contact_id, 0, 0, now(), now())"
                    " ON CONFLICT (store_id, contact_id) DO NOTHING"
                ),
                {"store_id": store_id, "contact_id": contact_id},
            )
            account = await self._session.scalar(stmt)
        assert account is not None  # 插入或既存，重查必得
        return account

    async def insert_entry(self, entry: StoreCreditLedger) -> StoreCreditLedger:
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def find_by_source(
        self,
        store_id: int,
        source_type: StoreCreditSourceType,
        source_id: int,
        entry_type: StoreCreditEntryType,
    ) -> StoreCreditLedger | None:
        stmt = select(StoreCreditLedger).where(
            StoreCreditLedger.store_id == store_id,
            StoreCreditLedger.source_type == source_type,
            StoreCreditLedger.source_id == source_id,
            StoreCreditLedger.entry_type == entry_type,
        )
        result: StoreCreditLedger | None = await self._session.scalar(stmt)
        return result

    async def get_entry(self, store_id: int, entry_id: int) -> StoreCreditLedger | None:
        """以 id 取本店分錄（沖正前重載持久列，不信任呼叫端物件）。"""
        stmt = select(StoreCreditLedger).where(
            StoreCreditLedger.id == entry_id, StoreCreditLedger.store_id == store_id
        )
        result: StoreCreditLedger | None = await self._session.scalar(stmt)
        return result

    async def find_reversal_of(self, store_id: int, original_id: int) -> StoreCreditLedger | None:
        """找某列的既有沖正（一列只能被沖一次；店別範圍雙保險）。"""
        stmt = select(StoreCreditLedger).where(
            StoreCreditLedger.store_id == store_id,
            StoreCreditLedger.reversal_of_id == original_id,
        )
        result: StoreCreditLedger | None = await self._session.scalar(stmt)
        return result

    async def find_by_idempotency_key(
        self, store_id: int, idempotency_key: str
    ) -> StoreCreditLedger | None:
        """以冪等鍵找分錄（MANUAL 校正防重複）。"""
        stmt = select(StoreCreditLedger).where(
            StoreCreditLedger.store_id == store_id,
            StoreCreditLedger.idempotency_key == idempotency_key,
        )
        result: StoreCreditLedger | None = await self._session.scalar(stmt)
        return result

    async def get_account(self, store_id: int, contact_id: int) -> StoreCreditAccount | None:
        stmt = select(StoreCreditAccount).where(
            StoreCreditAccount.store_id == store_id,
            StoreCreditAccount.contact_id == contact_id,
        )
        result: StoreCreditAccount | None = await self._session.scalar(stmt)
        return result

    async def list_entries(
        self, store_id: int, contact_id: int, *, limit: int = 50, offset: int = 0
    ) -> list[StoreCreditLedger]:
        stmt = (
            select(StoreCreditLedger)
            .where(
                StoreCreditLedger.store_id == store_id,
                StoreCreditLedger.contact_id == contact_id,
            )
            .order_by(StoreCreditLedger.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.scalars(stmt)).all())

    async def sum_signed(self, store_id: int, contact_id: int) -> Decimal:
        """帳本重算餘額（I-3 對帳：SUM == 快取 == 最新 balance_after）。"""
        stmt = select(func.coalesce(func.sum(StoreCreditLedger.signed_amount), 0)).where(
            StoreCreditLedger.store_id == store_id,
            StoreCreditLedger.contact_id == contact_id,
        )
        value = await self._session.scalar(stmt)
        return Decimal(value if value is not None else 0)

    async def latest_balance_after(self, store_id: int, contact_id: int) -> Decimal | None:
        stmt = (
            select(StoreCreditLedger.balance_after)
            .where(
                StoreCreditLedger.store_id == store_id,
                StoreCreditLedger.contact_id == contact_id,
            )
            .order_by(StoreCreditLedger.id.desc())
            .limit(1)
        )
        value = await self._session.scalar(stmt)
        return None if value is None else Decimal(value)

    async def rows_violating_chain(self, store_id: int) -> list[int]:
        """balance_after ≠ 滾動和的列（全鏈對帳；window 累計依 id 序）。"""
        stmt = text(
            "SELECT id FROM ("
            "  SELECT id, balance_after,"
            "         SUM(signed_amount) OVER ("
            "           PARTITION BY store_id, contact_id ORDER BY id"
            "         ) AS running"
            "  FROM store_credit_ledger WHERE store_id = :sid"
            ") chain WHERE balance_after <> running ORDER BY id"
        )
        result = await self._session.execute(stmt, {"sid": store_id})
        return [int(row[0]) for row in result]

    async def list_ledger_contacts(self, store_id: int) -> list[int]:
        """帳本中出現過的 contact（孤兒帳本偵測：與帳戶列做全比對）。"""
        stmt = (
            select(StoreCreditLedger.contact_id)
            .where(StoreCreditLedger.store_id == store_id)
            .distinct()
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_accounts(self, store_id: int) -> list[StoreCreditAccount]:
        stmt = select(StoreCreditAccount).where(StoreCreditAccount.store_id == store_id)
        return list((await self._session.scalars(stmt)).all())

    async def ledger_total_outstanding(self, store_id: int) -> Decimal:
        """帳本推導總負債 = Σ 各 contact 的正向帳本餘額（含孤兒帳本）。"""
        per_contact = (
            select(func.sum(StoreCreditLedger.signed_amount).label("bal"))
            .where(StoreCreditLedger.store_id == store_id)
            .group_by(StoreCreditLedger.contact_id)
            .subquery()
        )
        stmt = select(func.coalesce(func.sum(per_contact.c.bal), 0)).where(per_contact.c.bal > 0)
        value = await self._session.scalar(stmt)
        return Decimal(value if value is not None else 0)

    async def total_outstanding(self, store_id: int) -> Decimal:
        """全域總負債 = Σ 正餘額（docs/16 §4 對帳）。"""
        stmt = select(func.coalesce(func.sum(StoreCreditAccount.balance), 0)).where(
            StoreCreditAccount.store_id == store_id,
            StoreCreditAccount.balance > 0,
        )
        value = await self._session.scalar(stmt)
        return Decimal(value if value is not None else 0)

    # ── SC-4 報表查詢（read-only；docs/16 §5A）──

    async def positive_lots(self, store_id: int) -> list[tuple[int, Decimal, datetime]]:
        """各會員的「發出（正向）列」：(contact_id, signed_amount, created_at)，依會員、時間排序。

        供帳齡 FIFO 沖銷（任何正向 entry 皆視為發出列：CREDIT／正向 ADJUSTMENT／沖正回補）。
        已被沖正的原始 CREDIT（如 ACQUISITION_ROLLBACK 沖正的作廢收購入帳）排除——該額度已取消、
        非有效負債，不可續列入帳齡，否則作廢收購仍被當成有效發出列誤算帳齡（Codex F6.5）。
        """
        stmt = (
            select(
                StoreCreditLedger.contact_id,
                StoreCreditLedger.signed_amount,
                StoreCreditLedger.created_at,
            )
            .where(
                StoreCreditLedger.store_id == store_id,
                StoreCreditLedger.signed_amount > 0,
                self._not_reversed(),
            )
            .order_by(StoreCreditLedger.contact_id, StoreCreditLedger.created_at)
        )
        rows = await self._session.execute(stmt)
        return [(int(c), Decimal(a), d) for c, a, d in rows]

    async def positive_sum_by_contact(self, store_id: int) -> dict[int, Decimal]:
        """各會員 Σ 正向 entry（供算 consumed = Σ正向 − 餘額）。

        與 positive_lots 一致排除已被沖正的原始 CREDIT，否則作廢收購入帳會灌大 Σ正向、
        使 consumed 被高估（餘額已扣掉沖正額，Σ正向卻仍含原額）（Codex F6.5）。
        """
        stmt = (
            select(
                StoreCreditLedger.contact_id,
                func.coalesce(func.sum(StoreCreditLedger.signed_amount), 0),
            )
            .where(
                StoreCreditLedger.store_id == store_id,
                StoreCreditLedger.signed_amount > 0,
                self._not_reversed(),
            )
            .group_by(StoreCreditLedger.contact_id)
        )
        rows = await self._session.execute(stmt)
        return {int(c): Decimal(s) for c, s in rows}

    async def balances_by_contact(self, store_id: int) -> dict[int, Decimal]:
        """各會員快取餘額（含 0／負，呼叫端自行過濾）。"""
        stmt = select(StoreCreditAccount.contact_id, StoreCreditAccount.balance).where(
            StoreCreditAccount.store_id == store_id
        )
        rows = await self._session.execute(stmt)
        return {int(c): Decimal(b) for c, b in rows}

    # ── SC-5b §5B 指標查詢（read-only；docs/16 §5B）──

    @staticmethod
    def _not_reversed() -> ColumnElement[bool]:
        """「該帳本列未被任何沖正列指向」的相關子查詢條件（沖正後原列不再代表有效負債/兌付）。"""
        rev = aliased(StoreCreditLedger)
        return ~select(rev.id).where(rev.reversal_of_id == StoreCreditLedger.id).exists()

    async def credit_premium_components(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> tuple[Decimal, Decimal]:
        """期間「未被沖正」CREDIT 列的 (Σ signed, Σ cash_equivalent)，供 avg_premium 計算。

        ACQUISITION_ROLLBACK 沖正後原 CREDIT 已非有效負債，須排除否則 avg_premium 被高估
        （Codex SC-5b P2）。
        """
        stmt = select(
            func.coalesce(func.sum(StoreCreditLedger.signed_amount), 0),
            func.coalesce(func.sum(StoreCreditLedger.cash_equivalent), 0),
        ).where(
            StoreCreditLedger.store_id == store_id,
            StoreCreditLedger.entry_type == StoreCreditEntryType.CREDIT,
            StoreCreditLedger.created_at >= date_from,
            StoreCreditLedger.created_at < date_to,
            self._not_reversed(),
        )
        row = (await self._session.execute(stmt)).one()
        return Decimal(row[0]), Decimal(row[1])

    async def debits_in_period(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> list[tuple[int, Decimal]]:
        """期間「未被沖正」的 DEBIT 兌付列：(contact_id, 絕對金額)，供 α 代理逐筆分類。

        已作廢銷售的 DEBIT 留在 append-only 帳本、另有 SALE_VOID 沖正回補——那筆兌付實際
        未發生，必須排除，否則 redemption_count / α 被灌水（Codex SC-5b P2）。退貨不回寫
        原成交期的行為指標；即時負債 β 與流量淨額另由 REFUND 扣除。
        """
        stmt = select(StoreCreditLedger.contact_id, -StoreCreditLedger.signed_amount).where(
            StoreCreditLedger.store_id == store_id,
            StoreCreditLedger.entry_type == StoreCreditEntryType.DEBIT,
            StoreCreditLedger.created_at >= date_from,
            StoreCreditLedger.created_at < date_to,
            self._not_reversed(),
        )
        rows = await self._session.execute(stmt)
        return [(int(c), Decimal(a)) for c, a in rows]

    async def redeemed_against_credit_by_contact(self, store_id: int) -> dict[int, Decimal]:
        """各會員對 CREDIT 的「淨沖銷額」（β 沉澱率 FIFO 的 consumed）。

        = Σ|DEBIT| − Σ（SALE_VOID 沖正＋SALE_RETURN 退貨回補）。
        作廢／退貨回補的兌付淨額為 0，故 β 不會把已回補的額度誤算為已消耗。
        人工 ADJUSTMENT 不視為對 CREDIT lot 的兌付（β 只看銷售沖銷）。
        """
        stmt = (
            select(
                StoreCreditLedger.contact_id,
                func.coalesce(func.sum(-StoreCreditLedger.signed_amount), 0),
            )
            .where(
                StoreCreditLedger.store_id == store_id,
                or_(
                    StoreCreditLedger.entry_type == StoreCreditEntryType.DEBIT,
                    and_(
                        StoreCreditLedger.entry_type == StoreCreditEntryType.REVERSAL,
                        StoreCreditLedger.source_type == StoreCreditSourceType.SALE_VOID,
                    ),
                    and_(
                        StoreCreditLedger.entry_type == StoreCreditEntryType.REFUND,
                        StoreCreditLedger.source_type == StoreCreditSourceType.SALE_RETURN,
                    ),
                ),
            )
            .group_by(StoreCreditLedger.contact_id)
        )
        rows = await self._session.execute(stmt)
        return {int(c): Decimal(v) for c, v in rows}

    async def earliest_credit_at_by_contacts(
        self, store_id: int, contact_ids: list[int]
    ) -> dict[int, datetime]:
        """指定會員的最早 CREDIT 入帳時間（α 代理的「對應 CREDIT 入帳」參考時點，FIFO 近似）。"""
        if not contact_ids:
            return {}
        stmt = (
            select(StoreCreditLedger.contact_id, func.min(StoreCreditLedger.created_at))
            .where(
                StoreCreditLedger.store_id == store_id,
                StoreCreditLedger.entry_type == StoreCreditEntryType.CREDIT,
                StoreCreditLedger.contact_id.in_(contact_ids),
            )
            .group_by(StoreCreditLedger.contact_id)
        )
        rows = await self._session.execute(stmt)
        return {int(c): t for c, t in rows}

    async def credit_lots(self, store_id: int) -> list[tuple[int, Decimal, datetime]]:
        """「未被沖正」CREDIT 發出列：(contact_id, 金額, 發出時間)，依會員/時間排序（β FIFO 用）。

        ACQUISITION_ROLLBACK 沖正後的 CREDIT 已非有效負債，排除以免 β 分母含已撤回額度
        （Codex SC-5b P2）。
        """
        stmt = (
            select(
                StoreCreditLedger.contact_id,
                StoreCreditLedger.signed_amount,
                StoreCreditLedger.created_at,
            )
            .where(
                StoreCreditLedger.store_id == store_id,
                StoreCreditLedger.entry_type == StoreCreditEntryType.CREDIT,
                self._not_reversed(),
            )
            .order_by(StoreCreditLedger.contact_id, StoreCreditLedger.created_at)
        )
        rows = await self._session.execute(stmt)
        return [(int(c), Decimal(a), t) for c, a, t in rows]

    async def earliest_activity_at(self, store_id: int) -> datetime | None:
        """本店最早一筆帳本時間（供引擎判冷啟動的資料天數；無帳本 → None）。"""
        stmt = select(func.min(StoreCreditLedger.created_at)).where(
            StoreCreditLedger.store_id == store_id
        )
        result: datetime | None = await self._session.scalar(stmt)
        return result

    # ── SC-5b 建議值落庫（docs/16 §1.4；每店每日唯一）──

    async def get_suggestion_log(
        self, store_id: int, for_date: date
    ) -> StoreCreditSuggestionLog | None:
        stmt = select(StoreCreditSuggestionLog).where(
            StoreCreditSuggestionLog.store_id == store_id,
            StoreCreditSuggestionLog.for_date == for_date,
        )
        result: StoreCreditSuggestionLog | None = await self._session.scalar(stmt)
        return result

    async def add_suggestion_log(self, log: StoreCreditSuggestionLog) -> StoreCreditSuggestionLog:
        self._session.add(log)
        await self._session.flush()
        return log

    async def flows(
        self,
        store_id: int,
        *,
        date_from: datetime,
        date_to: datetime,
        granularity: str,
    ) -> list[tuple[date, Decimal, Decimal, Decimal, Decimal, Decimal]]:
        """期間內按粒度彙總流量的毛/沖正分量：
        (period, issued_gross, issued_reversed, redeemed_gross, redeemed_reversed, adjustment_net)。

        - issued_gross：CREDIT 發出毛額（>0）。
        - issued_reversed：ACQUISITION_ROLLBACK 沖正的發出額（取 -signed 轉正，>0）。
        - redeemed_gross：DEBIT 兌付毛額（取 -signed 轉正，>0）。
        - redeemed_reversed：SALE_VOID 沖正或 SALE_RETURN 退貨回補的兌付額（>0）。
        - adjustment_net：人工 ADJUSTMENT 的 signed 淨額（可正可負）。
        淨額由 service 推得：issued_net=gross-reversed、redeemed_net=gross-reversed、
        net_change=issued_net-redeemed_net+adjustment_net；如此 net_change 恰等於該期帳本完整
        signed 淨變化（CREDIT+DEBIT+REFUND+REVERSAL+ADJUSTMENT 全涵蓋），可與 liability 差額對上
        （docs/19 §3.1）。沖正落在沖正當期、毛額落發出/兌付當期（§3.3）。

        granularity 限 day/week/month（由 service 驗證後傳入；以參數綁定 date_trunc 單位）。
        """
        stmt = text(
            "SELECT date_trunc(:gran, created_at AT TIME ZONE :tz)::date AS period,"
            "  COALESCE(SUM(CASE WHEN entry_type='CREDIT'"
            "    THEN signed_amount ELSE 0 END), 0) AS issued_gross,"
            "  COALESCE(SUM(CASE WHEN entry_type='REVERSAL'"
            "      AND source_type='ACQUISITION_ROLLBACK'"
            "    THEN -signed_amount ELSE 0 END), 0) AS issued_reversed,"
            "  COALESCE(SUM(CASE WHEN entry_type='DEBIT'"
            "    THEN -signed_amount ELSE 0 END), 0) AS redeemed_gross,"
            "  COALESCE(SUM(CASE WHEN (entry_type='REVERSAL'"
            "      AND source_type='SALE_VOID') OR (entry_type='REFUND'"
            "      AND source_type='SALE_RETURN')"
            "    THEN signed_amount ELSE 0 END), 0) AS redeemed_reversed,"
            "  COALESCE(SUM(CASE WHEN entry_type='ADJUSTMENT'"
            "    THEN signed_amount ELSE 0 END), 0) AS adjustment_net"
            " FROM store_credit_ledger"
            " WHERE store_id = :sid AND created_at >= :dfrom AND created_at < :dto"
            " GROUP BY period ORDER BY period"
        )
        result = await self._session.execute(
            stmt,
            {
                "gran": granularity,
                "tz": STORE_TIME_ZONE_NAME,
                "sid": store_id,
                "dfrom": date_from,
                "dto": date_to,
            },
        )
        return [
            (p, Decimal(ig), Decimal(ir), Decimal(rg), Decimal(rr), Decimal(adj))
            for p, ig, ir, rg, rr, adj in result
        ]
