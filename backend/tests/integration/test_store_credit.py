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
    CrossStoreReference,
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
        credit.id,
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
        credit.id,
        source_type=StoreCreditSourceType.ACQUISITION_ROLLBACK,
        source_id=7,
        created_by=user_id,
    )
    assert again.id == reversal.id


async def test_reverse_same_row_with_different_source_conflicts(
    db_session: AsyncSession,
) -> None:
    """一列只能被沖一次（adversarial high）：不同來源試圖再沖 → 409、餘額不變。"""
    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    credit = await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(1000),
        premium_rate=Decimal("0.10"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=70,
        created_by=user_id,
    )
    await svc.reverse(
        store_id,
        credit.id,
        source_type=StoreCreditSourceType.ACQUISITION_ROLLBACK,
        source_id=70,
        created_by=user_id,
    )
    # 補一筆讓餘額足夠（排除「餘額不足」因素，專測重複沖正防護）
    await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(2000),
        premium_rate=Decimal("0.10"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=71,
        created_by=user_id,
    )
    before = await svc.get_balance(store_id, member_id)
    with pytest.raises(StoreCreditConflict):
        await svc.reverse(
            store_id,
            credit.id,
            source_type=StoreCreditSourceType.SALE_VOID,  # 不同來源
            source_id=999,
            created_by=user_id,
        )
    assert await svc.get_balance(store_id, member_id) == before


async def test_reverse_rejects_cross_store_original(db_session: AsyncSession) -> None:
    """多分店隔離（adversarial high）：他店的列不可在本店被沖正。"""
    store_id, user_id, member_id = await _seed(db_session)
    other_store = Store(name="B 店")
    db_session.add(other_store)
    await db_session.flush()
    svc = StoreCreditService(db_session)
    credit = await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(500),
        premium_rate=Decimal("0.10"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=88,
        created_by=user_id,
    )
    with pytest.raises(CrossStoreReference):
        await svc.reverse(
            other_store.id,
            credit.id,
            source_type=StoreCreditSourceType.ACQUISITION_ROLLBACK,
            source_id=88,
            created_by=user_id,
        )


async def test_debit_rejects_cross_store_contact(db_session: AsyncSession) -> None:
    """寫入路徑統一守店別：他店 contact 不可在本店建帳/扣抵。"""
    store_id, user_id, _ = await _seed(db_session)
    other_store = Store(name="B 店")
    db_session.add(other_store)
    await db_session.flush()
    foreign_member = Contact(store_id=other_store.id, name="他店會員", roles=["MEMBER"])
    db_session.add(foreign_member)
    await db_session.flush()
    with pytest.raises(CrossStoreReference):
        await StoreCreditService(db_session).debit(
            store_id,
            foreign_member.id,
            amount=Decimal(10),
            source_type=StoreCreditSourceType.SALE,
            source_id=55,
            created_by=user_id,
        )


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
            credit.id,
            source_type=StoreCreditSourceType.ACQUISITION_ROLLBACK,
            source_id=8,
            created_by=user_id,
        )


async def test_adjust_writes_audit_and_requires_reason(db_session: AsyncSession) -> None:
    """I-11：人工校正必填事由、寫稽核。"""
    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    entry = await svc.adjust(
        store_id,
        member_id,
        amount=Decimal(50),
        reason="開幕活動補發",
        created_by=user_id,
        idempotency_key="adj-k1",
    )
    assert entry.reason == "開幕活動補發"
    logs = (await db_session.scalars(select(AuditLog))).all()
    actions = [log.action for log in logs]
    assert "STORE_CREDIT_ADJUST" in actions
    with pytest.raises(StoreCreditConflict):
        await svc.adjust(
            store_id,
            member_id,
            amount=Decimal(10),
            reason="   ",
            created_by=user_id,
            idempotency_key="adj-k2",
        )


async def test_adjust_idempotent_by_key(db_session: AsyncSession) -> None:
    """人工校正冪等（adversarial 第三輪 high）：同鍵重送回原列、負債只變一次；
    同鍵不同內容 → 409。"""
    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    first = await svc.adjust(
        store_id,
        member_id,
        amount=Decimal(50),
        reason="補發",
        created_by=user_id,
        idempotency_key="same-key",
    )
    replay = await svc.adjust(
        store_id,
        member_id,
        amount=Decimal(50),
        reason="補發",
        created_by=user_id,
        idempotency_key="same-key",
    )
    assert replay.id == first.id
    assert await svc.get_balance(store_id, member_id) == Decimal(50)  # 只加一次
    with pytest.raises(StoreCreditConflict):
        await svc.adjust(
            store_id,
            member_id,
            amount=Decimal(999),
            reason="不同內容",
            created_by=user_id,
            idempotency_key="same-key",
        )


async def test_reverse_uses_persisted_amount_not_caller_object(
    db_session: AsyncSession,
) -> None:
    """沖正以持久列為準（adversarial 第五輪 high）：以 id 重載，呼叫端無從帶入
    偽造金額；沖正額恆等於原列負值。"""
    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    credit = await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(1000),
        premium_rate=Decimal("0.10"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=300,
        created_by=user_id,
    )
    reversal = await svc.reverse(
        store_id,
        credit.id,
        source_type=StoreCreditSourceType.ACQUISITION_ROLLBACK,
        source_id=300,
        created_by=user_id,
    )
    assert reversal.signed_amount == -Decimal(1100)  # 恆為持久列負值
    with pytest.raises(CrossStoreReference):
        await svc.reverse(  # 不存在的 id → 拒絕
            store_id,
            999999,
            source_type=StoreCreditSourceType.SALE_VOID,
            source_id=301,
            created_by=user_id,
        )
    with pytest.raises(StoreCreditConflict):
        await svc.reverse(  # 沖正列本身不可再沖
            store_id,
            reversal.id,
            source_type=StoreCreditSourceType.SALE_VOID,
            source_id=302,
            created_by=user_id,
        )


async def test_db_reversal_guard_rejects_wrong_amount_and_double_layer(
    db_session: AsyncSession,
) -> None:
    """DB 沖正跨列守衛（第十輪 high）：直插錯額沖正、沖沖正列 → 一律報錯。"""
    from sqlalchemy.exc import DBAPIError

    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    credit = await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(1000),
        premium_rate=Decimal("0.10"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=500,
        created_by=user_id,
    )

    async def _raw_reversal(rev_id: int, amount: int, src: int) -> None:
        await db_session.execute(
            text(
                "INSERT INTO store_credit_ledger"
                " (store_id, contact_id, entry_type, signed_amount, balance_after,"
                "  source_type, source_id, reversal_of_id, fingerprint, created_by, created_at)"
                " VALUES (:sid, :cid, 'REVERSAL', :amount, 0, 'SALE_VOID', :src, :rev,"
                "  'forged', :uid, now())"
            ),
            {
                "sid": store_id,
                "cid": member_id,
                "uid": user_id,
                "rev": rev_id,
                "amount": amount,
                "src": src,
            },
        )

    with pytest.raises(DBAPIError):
        await _raw_reversal(credit.id, -999, 501)  # 錯額（應為 -1100）
    await db_session.rollback()

    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    credit = await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(1000),
        premium_rate=Decimal("0.00"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=502,
        created_by=user_id,
    )
    reversal = await svc.reverse(
        store_id,
        credit.id,
        source_type=StoreCreditSourceType.ACQUISITION_ROLLBACK,
        source_id=502,
        created_by=user_id,
    )
    # 補回餘額，讓「沖沖正列」不會先被餘額守衛擋住，專測 DB 層
    await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(5000),
        premium_rate=Decimal("0.00"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=503,
        created_by=user_id,
    )
    with pytest.raises(DBAPIError):
        await _raw_reversal(reversal.id, 1000, 504)  # 沖沖正列
    await db_session.rollback()


async def test_db_credit_guard_rejects_forged_economics(db_session: AsyncSession) -> None:
    """DB CREDIT 經濟守衛（第十一輪 high）：實發 ≠ round(等值×(1+溢價)) 的直插報錯。"""
    from sqlalchemy.exc import DBAPIError

    store_id, user_id, member_id = await _seed(db_session)

    async def _raw_credit(amount: int, ce: int, rate: str, src: int) -> None:
        await db_session.execute(
            text(
                "INSERT INTO store_credit_ledger"
                " (store_id, contact_id, entry_type, signed_amount, balance_after,"
                "  cash_equivalent, premium_rate_applied, source_type, source_id,"
                "  fingerprint, created_by, created_at)"
                " VALUES (:sid, :cid, 'CREDIT', :amount, :amount, :ce, :rate,"
                "  'ACQUISITION', :src, 'forged', :uid, now())"
            ),
            {
                "sid": store_id,
                "cid": member_id,
                "uid": user_id,
                "amount": amount,
                "ce": ce,
                "rate": rate,
                "src": src,
            },
        )

    with pytest.raises(DBAPIError):
        await _raw_credit(999, 100, "0.1000", 601)  # 應為 110
    await db_session.rollback()
    store_id, user_id, member_id = await _seed(db_session)
    with pytest.raises(DBAPIError):
        await _raw_credit(150, 100, "0.5000", 602)  # 溢價超界
    await db_session.rollback()


async def test_db_check_rejects_wrong_direction(db_session: AsyncSession) -> None:
    """方向/形狀 CHECK（adversarial 第五輪 medium）：正額 DEBIT、無對象 REVERSAL
    直插一律 IntegrityError。"""
    from sqlalchemy.exc import IntegrityError

    store_id, user_id, member_id = await _seed(db_session)

    async def _raw(etype: str, amount: int, rev: int | None) -> None:
        await db_session.execute(
            text(
                "INSERT INTO store_credit_ledger"
                " (store_id, contact_id, entry_type, signed_amount, balance_after,"
                "  source_type, source_id, reversal_of_id, fingerprint, created_by, created_at)"
                " VALUES (:sid, :cid, :etype, :amount, 100, 'SALE', 1, :rev, 'x', :uid, now())"
            ),
            {
                "sid": store_id,
                "cid": member_id,
                "uid": user_id,
                "etype": etype,
                "amount": amount,
                "rev": rev,
            },
        )

    with pytest.raises(IntegrityError):
        await _raw("DEBIT", 10, None)  # DEBIT 必負
    await db_session.rollback()
    store_id, user_id, member_id = await _seed(db_session)
    with pytest.raises(IntegrityError):
        await _raw("REVERSAL", -10, None)  # REVERSAL 必有對象
    await db_session.rollback()


async def test_premium_rate_policy_bounds(db_session: AsyncSession) -> None:
    """溢價率政策界線（第八輪 medium）：負值/超過 20% 的 CREDIT 一律拒——
    超界會寫出自洽但違反政策的負債，I-3 抓不到。"""
    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    for bad_rate in (Decimal("-0.5000"), Decimal("0.2001"), Decimal("1.0000")):
        with pytest.raises(StoreCreditConflict):
            await svc.credit(
                store_id,
                member_id,
                cash_equivalent=Decimal(100),
                premium_rate=bad_rate,
                source_type=StoreCreditSourceType.ACQUISITION,
                source_id=450,
                created_by=user_id,
            )
    # 邊界值本身合法
    edge = await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(100),
        premium_rate=Decimal("0.2000"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=451,
        created_by=user_id,
    )
    assert edge.signed_amount == Decimal(120)


async def test_fractional_amounts_rejected(db_session: AsyncSession) -> None:
    """整數元守衛（adversarial 第六輪 high）：小數 debit/adjust/cash_equivalent 一律拒。"""
    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(100),
        premium_rate=Decimal("0.10"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=400,
        created_by=user_id,
    )
    with pytest.raises(StoreCreditConflict):
        await svc.debit(
            store_id,
            member_id,
            amount=Decimal("0.5"),
            source_type=StoreCreditSourceType.SALE,
            source_id=401,
            created_by=user_id,
        )
    with pytest.raises(StoreCreditConflict):
        await svc.adjust(
            store_id,
            member_id,
            amount=Decimal("1.5"),
            reason="小數",
            created_by=user_id,
            idempotency_key="frac",
        )
    with pytest.raises(StoreCreditConflict):
        await svc.credit(
            store_id,
            member_id,
            cash_equivalent=Decimal("99.5"),
            premium_rate=Decimal("0.10"),
            source_type=StoreCreditSourceType.ACQUISITION,
            source_id=402,
            created_by=user_id,
        )


async def test_adjust_retry_writes_audit_once(db_session: AsyncSession) -> None:
    """冪等重放不得重複寫稽核（adversarial 第四輪 high）：同鍵重試恰一筆 audit。"""
    store_id, user_id, member_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    for _ in range(3):
        await svc.adjust(
            store_id,
            member_id,
            amount=Decimal(50),
            reason="補發",
            created_by=user_id,
            idempotency_key="audit-once",
        )
    logs = (await db_session.scalars(select(AuditLog))).all()
    adjust_logs = [log for log in logs if log.action == "STORE_CREDIT_ADJUST"]
    assert len(adjust_logs) == 1


async def test_db_check_constraints_reject_invalid_states(db_session: AsyncSession) -> None:
    """DB CHECK（adversarial 第四輪 medium）：零金額/負 balance_after/缺 CREDIT 欄位
    的直插一律 IntegrityError。"""
    from sqlalchemy.exc import IntegrityError

    store_id, user_id, member_id = await _seed(db_session)

    async def _raw_insert(**overrides: object) -> None:
        params: dict[str, object] = {
            "sid": store_id,
            "cid": member_id,
            "uid": user_id,
            "etype": "DEBIT",
            "amount": -10,
            "after": 0,
            "ce": None,
            "rate": None,
            "fp": "x",
        }
        params.update(overrides)
        await db_session.execute(
            text(
                "INSERT INTO store_credit_ledger"
                " (store_id, contact_id, entry_type, signed_amount, balance_after,"
                "  cash_equivalent, premium_rate_applied, source_type, source_id,"
                "  fingerprint, created_by, created_at)"
                " VALUES (:sid, :cid, :etype, :amount, :after, :ce, :rate,"
                "  'MANUAL', NULL, :fp, :uid, now())"
            ),
            params,
        )

    with pytest.raises(IntegrityError):
        await _raw_insert(amount=0)  # signed_amount <> 0
    await db_session.rollback()
    store_id, user_id, member_id = await _seed(db_session)
    with pytest.raises(IntegrityError):
        await _raw_insert(after=-1, sid=store_id, cid=member_id, uid=user_id)  # balance_after >= 0
    await db_session.rollback()
    store_id, user_id, member_id = await _seed(db_session)
    with pytest.raises(IntegrityError):
        await _raw_insert(  # CREDIT 必帶 cash_equivalent/premium
            etype="CREDIT", amount=100, after=100, sid=store_id, cid=member_id, uid=user_id
        )
    await db_session.rollback()


async def test_db_rejects_cross_tenant_reversal_insert(db_session: AsyncSession) -> None:
    """持久層沖正租戶綁定（adversarial 第三輪 high）：直插「B 店沖 A 店列」被
    複合自參考 FK 擋。"""
    store_id, user_id, member_id = await _seed(db_session)
    other_store = Store(name="B 店")
    db_session.add(other_store)
    await db_session.flush()
    other_member = Contact(store_id=other_store.id, name="B 店會員", roles=["MEMBER"])
    other_user = User(
        store_id=other_store.id, username="b-mgr", password_hash="h", role=UserRole.MANAGER
    )
    db_session.add_all([other_member, other_user])
    await db_session.flush()
    svc = StoreCreditService(db_session)
    original = await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(100),
        premium_rate=Decimal("0.10"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=77,
        created_by=user_id,
    )
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO store_credit_ledger"
                " (store_id, contact_id, entry_type, signed_amount, balance_after,"
                "  source_type, source_id, reversal_of_id, fingerprint, created_by, created_at)"
                " VALUES (:sid, :cid, 'REVERSAL', -110, 0, 'SALE_VOID', 9999, :rev,"
                "  'forged', :uid, now())"
            ),
            {
                "sid": other_store.id,
                "cid": other_member.id,
                "rev": original.id,
                "uid": other_user.id,
            },
        )
    await db_session.rollback()


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
    assert clean["ledger_total_outstanding"] == "110"
    assert clean["cached_total_outstanding"] == "110"
    assert clean["cached_total_trustworthy"] is True
    # 竄改快取（帳本不可改）
    await db_session.execute(
        text("UPDATE store_credit_accounts SET balance = 999 WHERE store_id = :sid"),
        {"sid": store_id},
    )
    dirty = await svc.reconcile(store_id)
    assert len(dirty["mismatches"]) == 1  # type: ignore[arg-type]
    # 快取被竄改時：帳本推導總額仍正確、快取值標記不可信（第八輪 high）
    assert dirty["ledger_total_outstanding"] == "110"
    assert dirty["cached_total_outstanding"] == "999"
    assert dirty["cached_total_trustworthy"] is False


async def test_db_rejects_cross_store_contact_pairing(db_session: AsyncSession) -> None:
    """持久層租戶約束（adversarial medium）：直插「A 店帳配 B 店 contact」被複合 FK 擋。"""
    store_id, user_id, _ = await _seed(db_session)
    other_store = Store(name="B 店")
    db_session.add(other_store)
    await db_session.flush()
    foreign = Contact(store_id=other_store.id, name="他店客", roles=["MEMBER"])
    db_session.add(foreign)
    await db_session.flush()
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO store_credit_accounts"
                " (store_id, contact_id, balance, version, created_at, updated_at)"
                " VALUES (:sid, :cid, 0, 0, now(), now())"
            ),
            {"sid": store_id, "cid": foreign.id},
        )
    await db_session.rollback()


async def test_concurrent_same_source_credits_idempotent() -> None:
    """並發同來源同內容入帳：恰一列、餘額只加一次，兩邊都拿到同一分錄（不冒 500）。"""
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="冪等競態店")
        s.add(store)
        await s.flush()
        user = User(
            store_id=store.id, username="race-idem", password_hash="h", role=UserRole.MANAGER
        )
        member = Contact(store_id=store.id, name="冪等會員", roles=["MEMBER"])
        s.add_all([user, member])
        await s.flush()
        store_id, user_id, member_id = store.id, user.id, member.id
        await s.commit()

    try:

        async def _credit_once() -> int:
            async with sm() as s:
                entry = await StoreCreditService(s).credit(
                    store_id,
                    member_id,
                    cash_equivalent=Decimal(1000),
                    premium_rate=Decimal("0.1000"),
                    source_type=StoreCreditSourceType.ACQUISITION,
                    source_id=500,
                    created_by=user_id,
                )
                await s.commit()
                return entry.id

        ids = await asyncio.gather(_credit_once(), _credit_once())
        assert ids[0] == ids[1]  # 兩邊拿到同一列
        async with sm() as s:
            svc = StoreCreditService(s)
            assert await svc.get_balance(store_id, member_id) == Decimal(1100)  # 只加一次
            assert (await svc.reconcile(store_id))["mismatches"] == []
    finally:
        async with sm() as s:
            await s.execute(text("TRUNCATE store_credit_ledger, store_credit_accounts"))
            await s.execute(text("DELETE FROM audit_log"))
            await s.execute(text("DELETE FROM contacts"))
            await s.execute(text("DELETE FROM users"))
            await s.execute(text("DELETE FROM stores"))
            await s.commit()


async def test_concurrent_reversals_of_same_row_safe() -> None:
    """並發沖正同一列（同來源重試情境）：恰沖一次、兩邊拿到同一沖正列。"""
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="沖正競態店")
        s.add(store)
        await s.flush()
        user = User(
            store_id=store.id, username="race-rev", password_hash="h", role=UserRole.MANAGER
        )
        member = Contact(store_id=store.id, name="沖正會員", roles=["MEMBER"])
        s.add_all([user, member])
        await s.flush()
        credit = await StoreCreditService(s).credit(
            store.id,
            member.id,
            cash_equivalent=Decimal(1000),
            premium_rate=Decimal("0.0000"),
            source_type=StoreCreditSourceType.ACQUISITION,
            source_id=600,
            created_by=user.id,
        )
        store_id, user_id, member_id, credit_id = store.id, user.id, member.id, credit.id
        await s.commit()

    try:

        async def _reverse_once() -> int:
            async with sm() as s:
                svc = StoreCreditService(s)
                entry = await svc.reverse(
                    store_id,
                    credit_id,
                    source_type=StoreCreditSourceType.ACQUISITION_ROLLBACK,
                    source_id=600,
                    created_by=user_id,
                )
                await s.commit()
                return entry.id

        ids = await asyncio.gather(_reverse_once(), _reverse_once())
        assert ids[0] == ids[1]
        async with sm() as s:
            svc = StoreCreditService(s)
            assert await svc.get_balance(store_id, member_id) == Decimal(0)  # 只沖一次
            assert (await svc.reconcile(store_id))["mismatches"] == []
    finally:
        async with sm() as s:
            await s.execute(text("TRUNCATE store_credit_ledger, store_credit_accounts"))
            await s.execute(text("DELETE FROM audit_log"))
            await s.execute(text("DELETE FROM contacts"))
            await s.execute(text("DELETE FROM users"))
            await s.execute(text("DELETE FROM stores"))
            await s.commit()


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
