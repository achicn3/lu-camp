"""身分簡化 migration（c4e8a2b6d9f1）的資料改寫行為。

migration 一旦跑過就不會再跑第二次，出錯的資料要人工修——所以它的 SQL 值得單獨釘住。
這裡直接對真的資料庫執行那兩段 SQL（而非整條 alembic 鏈），驗四件事：
  1. CONSIGNOR 併入 SELLER 且去重
  2. 每個人補上 MEMBER，含原本沒有任何角色的列（不可變成 NULL）
  3. 有身分證字號的歷史賣方被回填 SELLER
  4. **證號已清除的歷史賣方不回填**——否則造出「賣方卻無證號」，
     正是防贓物登記要求禁止的（Codex 第二輪對抗式審查，實測可重現）
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.contacts.models import Contact
from app.modules.store.models import Store

MIGRATION = "c4e8a2b6d9f1_contacts_single_role.py"


def _load() -> ModuleType:
    path = Path(__file__).parents[2] / "alembic" / "versions" / MIGRATION
    spec = importlib.util.spec_from_file_location("contacts_single_role_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_downgrade_is_explicitly_irreversible() -> None:
    """合併後無從得知原本是賣方還是寄售人；硬回填只會捏造歷史。"""
    with pytest.raises(NotImplementedError, match="不可逆"):
        _load().downgrade()


async def _rewrite(session: AsyncSession) -> None:
    """執行 migration **本體**的兩段 SQL（不是複製品）。

    先前這裡自己抄了一份 SQL，結果是：改了複製品測試毫無反應，改壞真的 migration 也
    照樣全綠——那種測試比沒有更糟，因為它讓人以為 migration 被守著。
    """
    migration = _load()
    await session.execute(text(migration.MERGE_ROLES_SQL))
    await session.execute(text(migration.BACKFILL_SELLERS_SQL))


async def test_rewrite_merges_roles_and_never_leaves_a_seller_without_an_id(
    db_session: AsyncSession,
) -> None:
    store = Store(name="遷移店")
    db_session.add(store)
    await db_session.flush()

    consignor = Contact(store_id=store.id, name="舊寄售人", roles=["CONSIGNOR"], phone="0900000001")
    both = Contact(
        store_id=store.id, name="舊賣方兼寄售", roles=["SELLER", "CONSIGNOR"], phone="0900000002"
    )
    roleless = Contact(store_id=store.id, name="沒有角色", roles=[], phone="0900000003")
    db_session.add_all([consignor, both, roleless])
    await db_session.flush()

    await _rewrite(db_session)
    for row in (consignor, both, roleless):
        await db_session.refresh(row)

    assert consignor.roles == ["MEMBER", "SELLER"]
    assert both.roles == ["MEMBER", "SELLER"]  # 去重：換完會有兩個 SELLER
    assert roleless.roles == ["MEMBER"]  # 空陣列不可變成 NULL（欄位 NOT NULL）


async def test_backfill_skips_sellers_whose_id_was_cleared(db_session: AsyncSession) -> None:
    """曾收購但身分證字號後來被清掉的人**不得**被回填成賣方。

    回填的用意是讓「賣方」名副其實（賣過東西給店裡）。但補上標記卻沒有證號，就造出
    「賣方卻無身分證字號」——那是防贓物登記要求明令禁止的狀態，而且 migration 跑完
    才發現就只能人工修。寧可少標一個人（他的收購紀錄仍在，查得到）。
    """
    from datetime import UTC, datetime
    from decimal import Decimal

    from app.modules.acquisition.models import Acquisition
    from app.modules.user.models import User
    from app.shared.enums import AcquisitionType, PayoutMethod, UserRole

    store = Store(name="回填店")
    db_session.add(store)
    await db_session.flush()
    clerk = User(store_id=store.id, username="mig-clk", password_hash="h", role=UserRole.CLERK)
    db_session.add(clerk)
    await db_session.flush()

    cleared = Contact(
        store_id=store.id, name="證號已清除", roles=["MEMBER"], phone="0900000004"
    )
    kept = Contact(
        store_id=store.id,
        name="證號還在",
        roles=["MEMBER"],
        phone="0900000005",
        national_id_enc="enc",
        national_id_blind_index="idx",
    )
    never_sold = Contact(
        store_id=store.id,
        name="有證號但沒賣過",
        roles=["MEMBER"],
        phone="0900000006",
        national_id_enc="enc2",
        national_id_blind_index="idx2",
    )
    db_session.add_all([cleared, kept, never_sold])
    await db_session.flush()
    for contact in (cleared, kept):
        db_session.add(
            Acquisition(
                store_id=store.id,
                contact_id=contact.id,
                clerk_user_id=clerk.id,
                type=AcquisitionType.BUYOUT,
                payout_method=PayoutMethod.CASH,
                total_cash_paid=Decimal(100),
                payout_cash_amount=Decimal(100),
                payout_credit_cash_equivalent=Decimal(0),
                created_at=datetime.now(UTC),
            )
        )
    await db_session.flush()

    await _rewrite(db_session)
    for row in (cleared, kept, never_sold):
        await db_session.refresh(row)

    assert cleared.roles == ["MEMBER"], "沒有證號的人不可被回填成賣方"
    assert kept.roles == ["MEMBER", "SELLER"], "有證號的歷史賣方仍要回填"
    # 有證號但**從沒賣過東西**的純消費會員不可被回填——回填的依據是收購紀錄，
    # 不是「有沒有留證號」。少了這個斷言，把 EXISTS 條件整個拿掉也不會被發現。
    assert never_sold.roles == ["MEMBER"], "沒有收購紀錄的人不可被標成賣方"
