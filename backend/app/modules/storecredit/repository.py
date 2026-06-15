"""storecredit 資料存取層（唯一直接碰 ORM 的層）。

帳本只提供 INSERT（I-1）；帳戶列以 SELECT … FOR UPDATE 取得（寫入序列化錨點，
沿 D-1 模式）。餘額重算（SUM）供 I-3 對帳。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

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
            )
            .order_by(StoreCreditLedger.contact_id, StoreCreditLedger.created_at)
        )
        rows = await self._session.execute(stmt)
        return [(int(c), Decimal(a), d) for c, a, d in rows]

    async def positive_sum_by_contact(self, store_id: int) -> dict[int, Decimal]:
        """各會員 Σ 正向 entry（供算 consumed = Σ正向 − 餘額）。"""
        stmt = (
            select(
                StoreCreditLedger.contact_id,
                func.coalesce(func.sum(StoreCreditLedger.signed_amount), 0),
            )
            .where(
                StoreCreditLedger.store_id == store_id,
                StoreCreditLedger.signed_amount > 0,
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

    async def credit_premium_components(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> tuple[Decimal, Decimal]:
        """期間 CREDIT 列的 (Σ signed, Σ cash_equivalent)；avg_premium=(Σsigned−Σcash)÷Σcash。"""
        stmt = select(
            func.coalesce(func.sum(StoreCreditLedger.signed_amount), 0),
            func.coalesce(func.sum(StoreCreditLedger.cash_equivalent), 0),
        ).where(
            StoreCreditLedger.store_id == store_id,
            StoreCreditLedger.entry_type == StoreCreditEntryType.CREDIT,
            StoreCreditLedger.created_at >= date_from,
            StoreCreditLedger.created_at < date_to,
        )
        row = (await self._session.execute(stmt)).one()
        return Decimal(row[0]), Decimal(row[1])

    async def debits_in_period(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> list[tuple[int, Decimal]]:
        """期間 DEBIT 兌付列：(contact_id, 絕對金額)，供 α 代理逐筆分類。"""
        stmt = select(
            StoreCreditLedger.contact_id, -StoreCreditLedger.signed_amount
        ).where(
            StoreCreditLedger.store_id == store_id,
            StoreCreditLedger.entry_type == StoreCreditEntryType.DEBIT,
            StoreCreditLedger.created_at >= date_from,
            StoreCreditLedger.created_at < date_to,
        )
        rows = await self._session.execute(stmt)
        return [(int(c), Decimal(a)) for c, a in rows]

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
        """全部 CREDIT 發出列：(contact_id, 金額, 發出時間)，依會員/時間排序（β FIFO 用）。"""
        stmt = (
            select(
                StoreCreditLedger.contact_id,
                StoreCreditLedger.signed_amount,
                StoreCreditLedger.created_at,
            )
            .where(
                StoreCreditLedger.store_id == store_id,
                StoreCreditLedger.entry_type == StoreCreditEntryType.CREDIT,
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

    async def add_suggestion_log(
        self, log: StoreCreditSuggestionLog
    ) -> StoreCreditSuggestionLog:
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
    ) -> list[tuple[datetime, Decimal, Decimal]]:
        """期間內按粒度彙總：(period, issued=ΣCREDIT, redeemed=Σ|DEBIT|)。

        granularity 限 day/week/month（由 service 驗證後傳入；以參數綁定 date_trunc 單位）。
        """
        stmt = text(
            "SELECT date_trunc(:gran, created_at) AS period,"
            "  COALESCE(SUM(CASE WHEN entry_type='CREDIT'"
            "    THEN signed_amount ELSE 0 END), 0) AS issued,"
            "  COALESCE(SUM(CASE WHEN entry_type='DEBIT'"
            "    THEN -signed_amount ELSE 0 END), 0) AS redeemed"
            " FROM store_credit_ledger"
            " WHERE store_id = :sid AND created_at >= :dfrom AND created_at < :dto"
            " GROUP BY period ORDER BY period"
        )
        result = await self._session.execute(
            stmt,
            {
                "gran": granularity,
                "sid": store_id,
                "dfrom": date_from,
                "dto": date_to,
            },
        )
        return [(p, Decimal(i), Decimal(r)) for p, i, r in result]
