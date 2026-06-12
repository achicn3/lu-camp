"""購物金帳本核心測試（SC-1；docs/16 §2 不變量 I-1～I-11、ADR-012）。"""

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.db as app_db
from app.core.audit import AuditLog
from app.modules.contacts.models import Contact
from app.modules.store.models import Store
from app.modules.storecredit.models import StoreCreditLedger
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import (
    StoreCreditSourceType,
    UserRole,
)
from app.shared.exceptions import (
    InsufficientStoreCredit,
    StoreCreditConflict,
    StoreCreditMemberRequired,
)


async def _seed(session: AsyncSession) -> tuple[int, int, int]:
    """建店/MANAGER/會員，回 (store_id, user_id, member_contact_id)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    user = User(store_id=store.id, username="mgr-sc", password_hash="h", role=UserRole.MANAGER)
    member = Contact(store_id=store.id, name="會員甲", roles=["MEMBER"])
    session.add_all([user, member])
    await session.flush()
    return store.id, user.id, member.id


async def test_credit_applies_premium_and_records_three_values(
    db_session: AsyncSession,
) -> None:
    """I-4：實發 = round_ntd(現金等值 × (1+溢價))，三值同列可重現。"""
    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    entry = await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(1000),
        premium_rate=Decimal("0.1000"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=11,
        created_by=user_id,
    )
    assert entry.signed_amount == Decimal(1100)
    assert entry.cash_equivalent == Decimal(1000)
    assert entry.premium_rate_applied == Decimal("0.1000")
    assert entry.balance_after == Decimal(1100)
    assert await svc.get_balance(store_id, member_id) == Decimal(1100)


async def test_debit_and_balance_chain(db_session: AsyncSession) -> None:
    """I-2：balance_after 滾動正確；DEBIT 負向。"""
    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(1000),
        premium_rate=Decimal("0.10"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=1,
        created_by=user_id,
    )
    debited = await svc.debit(
        store_id,
        member_id,
        amount=Decimal(300),
        source_type=StoreCreditSourceType.SALE,
        source_id=2,
        created_by=user_id,
    )
    assert debited.signed_amount == Decimal(-300)
    assert debited.balance_after == Decimal(800)
    assert await svc.get_balance(store_id, member_id) == Decimal(800)


async def test_debit_over_balance_raises(db_session: AsyncSession) -> None:
    """I-6：永不負餘額。"""
    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    with pytest.raises(InsufficientStoreCredit):
        await svc.debit(
            store_id,
            member_id,
            amount=Decimal(1),
            source_type=StoreCreditSourceType.SALE,
            source_id=9,
            created_by=user_id,
        )


async def test_credit_requires_member(db_session: AsyncSession) -> None:
    """I-8：非會員不可持有購物金。"""
    store_id, user_id, _ = await _seed(db_session)
    non_member = Contact(store_id=store_id, name="散客", roles=[])
    db_session.add(non_member)
    await db_session.flush()
    svc = StoreCreditService(db_session)
    with pytest.raises(StoreCreditMemberRequired):
        await svc.credit(
            store_id,
            non_member.id,
            cash_equivalent=Decimal(100),
            premium_rate=Decimal("0.10"),
            source_type=StoreCreditSourceType.ACQUISITION,
            source_id=5,
            created_by=user_id,
        )


async def test_idempotent_same_source_returns_original(db_session: AsyncSession) -> None:
    """I-5：同來源同內容重送 → 回原列、不重複入帳。"""
    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    first = await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(500),
        premium_rate=Decimal("0.10"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=42,
        created_by=user_id,
    )
    replay = await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(500),
        premium_rate=Decimal("0.10"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=42,
        created_by=user_id,
    )
    assert replay.id == first.id
    assert await svc.get_balance(store_id, member_id) == Decimal(550)


async def test_idempotent_same_source_different_content_conflicts(
    db_session: AsyncSession,
) -> None:
    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(500),
        premium_rate=Decimal("0.10"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=42,
        created_by=user_id,
    )
    with pytest.raises(StoreCreditConflict):
        await svc.credit(
            store_id,
            member_id,
            cash_equivalent=Decimal(999),
            premium_rate=Decimal("0.10"),
            source_type=StoreCreditSourceType.ACQUISITION,
            source_id=42,
            created_by=user_id,
        )


async def test_reverse_credit_and_only_once(db_session: AsyncSession) -> None:
    """沖正方向相反、同來源只能沖一次；I-7 reversal_of_id 可追溯。"""
    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    credit = await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(1000),
        premium_rate=Decimal("0.10"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=7,
        created_by=user_id,
    )
    reversal = await svc.reverse(
        store_id,
        credit,
        source_type=StoreCreditSourceType.ACQUISITION_ROLLBACK,
        source_id=7,
        created_by=user_id,
    )
    assert reversal.signed_amount == Decimal(-1100)
    assert reversal.reversal_of_id == credit.id
    assert await svc.get_balance(store_id, member_id) == Decimal(0)
    # 同來源再沖 → 冪等回原列（內容相同）
    again = await svc.reverse(
        store_id,
        credit,
        source_type=StoreCreditSourceType.ACQUISITION_ROLLBACK,
        source_id=7,
        created_by=user_id,
    )
    assert again.id == reversal.id


async def test_reverse_credit_blocked_when_already_spent(db_session: AsyncSession) -> None:
    """docs/16 §3.3：已花掉 → 沖回會負 → 擋下（轉人工）。"""
    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    credit = await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(1000),
        premium_rate=Decimal("0.10"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=8,
        created_by=user_id,
    )
    await svc.debit(
        store_id,
        member_id,
        amount=Decimal(900),
        source_type=StoreCreditSourceType.SALE,
        source_id=80,
        created_by=user_id,
    )
    with pytest.raises(InsufficientStoreCredit):
        await svc.reverse(
            store_id,
            credit,
            source_type=StoreCreditSourceType.ACQUISITION_ROLLBACK,
            source_id=8,
            created_by=user_id,
        )


async def test_adjust_writes_audit_and_requires_reason(db_session: AsyncSession) -> None:
    """I-11：人工校正必填事由、寫稽核。"""
    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    entry = await svc.adjust(
        store_id, member_id, amount=Decimal(50), reason="開幕活動補發", created_by=user_id
    )
    assert entry.reason == "開幕活動補發"
    logs = (await db_session.scalars(select(AuditLog))).all()
    actions = [log.action for log in logs]
    assert "STORE_CREDIT_ADJUST" in actions
    with pytest.raises(StoreCreditConflict):
        await svc.adjust(store_id, member_id, amount=Decimal(10), reason="   ", created_by=user_id)


async def test_ledger_is_immutable_at_db_level(db_session: AsyncSession) -> None:
    """I-1：DB trigger 拒絕 UPDATE/DELETE（雙保險的第二道）。"""
    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    entry = await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(100),
        premium_rate=Decimal("0.10"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=66,
        created_by=user_id,
    )
    with pytest.raises(DBAPIError):
        await db_session.execute(
            update(StoreCreditLedger)
            .where(StoreCreditLedger.id == entry.id)
            .values(signed_amount=Decimal(999999))
        )
    await db_session.rollback()


async def test_reconcile_reports_mismatch_without_fixing(db_session: AsyncSession) -> None:
    """I-3：對帳發現快取被竄改 → 回報、不靜默修正。"""
    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(100),
        premium_rate=Decimal("0.10"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=3,
        created_by=user_id,
    )
    clean = await svc.reconcile(store_id)
    assert clean["mismatches"] == []
    assert clean["total_outstanding"] == "110"
    # 竄改快取（帳本不可改）
    await db_session.execute(
        text("UPDATE store_credit_accounts SET balance = 999 WHERE store_id = :sid"),
        {"sid": store_id},
    )
    dirty = await svc.reconcile(store_id)
    assert len(dirty["mismatches"]) == 1  # type: ignore[arg-type]


async def test_concurrent_debits_never_oversell() -> None:
    """I-7 並發：兩個並行扣抵合計超過餘額 → 恰一個成功（帳戶列鎖序列化）。

    比照 D-1 race 測試：獨立 sessionmaker、真 commit、finally 清理。
    """
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="購物金競態店")
        s.add(store)
        await s.flush()
        user = User(store_id=store.id, username="race-sc", password_hash="h", role=UserRole.MANAGER)
        member = Contact(store_id=store.id, name="競態會員", roles=["MEMBER"])
        s.add_all([user, member])
        await s.flush()
        await StoreCreditService(s).credit(
            store.id,
            member.id,
            cash_equivalent=Decimal(1000),
            premium_rate=Decimal("0.00"),
            source_type=StoreCreditSourceType.ACQUISITION,
            source_id=99,
            created_by=user.id,
        )
        store_id, user_id, member_id = store.id, user.id, member.id
        await s.commit()

    try:

        async def _try_debit(source_id: int) -> bool:
            async with sm() as s:
                try:
                    await StoreCreditService(s).debit(
                        store_id,
                        member_id,
                        amount=Decimal(700),
                        source_type=StoreCreditSourceType.SALE,
                        source_id=source_id,
                        created_by=user_id,
                    )
                    await s.commit()
                    return True
                except InsufficientStoreCredit:
                    await s.rollback()
                    return False

        results = await asyncio.gather(_try_debit(101), _try_debit(102))
        assert results.count(True) == 1  # 700+700 > 1000：恰一個成功

        async with sm() as s:
            balance = await StoreCreditService(s).get_balance(store_id, member_id)
            assert balance == Decimal(300)
            report = await StoreCreditService(s).reconcile(store_id)
            assert report["mismatches"] == []  # 並發後帳本/快取/最新列三方一致
    finally:
        async with sm() as s:
            # 帳本 insert-only trigger 連 DELETE 都擋（正確行為）；
            # 測試清理用 TRUNCATE（不觸發列級 trigger）。
            await s.execute(text("TRUNCATE store_credit_ledger, store_credit_accounts"))
            await s.execute(text("DELETE FROM audit_log"))
            await s.execute(text("DELETE FROM contacts"))
            await s.execute(text("DELETE FROM users"))
            await s.execute(text("DELETE FROM stores"))
            await s.commit()
