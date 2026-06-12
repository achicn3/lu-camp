"""storecredit 資料存取層（唯一直接碰 ORM 的層）。

帳本只提供 INSERT（I-1）；帳戶列以 SELECT … FOR UPDATE 取得（寫入序列化錨點，
沿 D-1 模式）。餘額重算（SUM）供 I-3 對帳。
"""

from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.storecredit.models import StoreCreditAccount, StoreCreditLedger
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

    async def total_outstanding(self, store_id: int) -> Decimal:
        """全域總負債 = Σ 正餘額（docs/16 §4 對帳）。"""
        stmt = select(func.coalesce(func.sum(StoreCreditAccount.balance), 0)).where(
            StoreCreditAccount.store_id == store_id,
            StoreCreditAccount.balance > 0,
        )
        value = await self._session.scalar(stmt)
        return Decimal(value if value is not None else 0)
