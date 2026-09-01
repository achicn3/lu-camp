"""聯絡人只有一種身分：每個人都是會員，賣過東西的人另外帶「賣方」標記（店主裁示）。

改動前有三種角色（MEMBER/SELLER/CONSIGNOR），建檔時要店員自己勾。實務上：
- 「賣方」與「寄售人」在程式裡的待遇**完全一樣**（都必須有身分證字號），分開只是
  多一個要店員判斷的欄位；商品是買斷來的還是寄售的，是**商品的屬性**（見 inventory
  的 source_kind），不是人的屬性。
- 純消費會員佔 72%（一年模擬 3081 人中 2204 人從沒賣過東西）。要他們留身分證字號
  才能集點，個資責任與辦卡阻力都不划算——身分證字號改成「第一次賣東西時才要」。

於是：建檔不問角色、一律是 MEMBER；賣東西時系統自動補上 SELLER。
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.contacts.schemas import ContactCreate
from app.modules.contacts.service import ContactService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import ContactRole, UserRole


async def _store_and_clerk(session: AsyncSession) -> tuple[int, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    return store.id, clerk.id


def test_role_enum_has_only_member_and_seller() -> None:
    """CONSIGNOR 已併入 SELLER：留著會讓人以為還要分辨買斷與寄售。"""
    assert {r.value for r in ContactRole} == {"MEMBER", "SELLER"}


def test_new_contact_is_a_member_without_asking() -> None:
    """建檔不問角色——店員少一個要判斷的欄位，而且判斷錯了沒有任何好處。"""
    created = ContactCreate(name="王小明", phone="0912345678")

    assert created.roles == [ContactRole.MEMBER]


def test_member_alone_does_not_require_national_id() -> None:
    """純消費會員不必留身分證字號（72% 的人屬於此類）。"""
    ContactCreate(name="王小明", phone="0912345678")  # 不拋錯即通過


def test_seller_still_requires_national_id() -> None:
    """收購要登記賣方身分是防贓物的必要程序，這條不放寬。"""
    with pytest.raises(ValueError, match="身分證"):
        ContactCreate(name="王小明", phone="0912345678", roles=[ContactRole.SELLER])


async def test_acquisition_tags_the_seller_automatically(db_session: AsyncSession) -> None:
    """賣東西給店裡 → 系統自動補上賣方標記，店員不必手動勾。"""
    store_id, clerk_id = await _store_and_clerk(db_session)
    svc = ContactService(db_session)
    contact = await svc.create_contact(
        store_id, ContactCreate(name="賣家甲", phone="0900111222", national_id="A123456789")
    )
    assert ContactRole.SELLER.value not in contact.roles  # 建檔當下只是會員

    await svc.ensure_seller_role(store_id, contact.id, actor_user_id=clerk_id)

    refreshed = await svc.get_contact(store_id, contact.id)
    assert set(refreshed.roles) == {ContactRole.MEMBER.value, ContactRole.SELLER.value}


async def test_tagging_twice_is_harmless(db_session: AsyncSession) -> None:
    """同一人賣第二次不得重複加標記——roles 是集合語意。"""
    store_id, clerk_id = await _store_and_clerk(db_session)
    svc = ContactService(db_session)
    contact = await svc.create_contact(
        store_id, ContactCreate(name="賣家乙", phone="0900111333", national_id="A123456789")
    )

    await svc.ensure_seller_role(store_id, contact.id, actor_user_id=clerk_id)
    await svc.ensure_seller_role(store_id, contact.id, actor_user_id=clerk_id)

    refreshed = await svc.get_contact(store_id, contact.id)
    assert sorted(refreshed.roles) == sorted([ContactRole.MEMBER.value, ContactRole.SELLER.value])


async def test_tagging_requires_a_national_id(db_session: AsyncSession) -> None:
    """沒有身分證字號的人不可被標成賣方——那會讓收購繞過防贓物的登記要求。"""
    store_id, clerk_id = await _store_and_clerk(db_session)
    svc = ContactService(db_session)
    contact = await svc.create_contact(
        store_id, ContactCreate(name="純會員", phone="0900111444")
    )

    with pytest.raises(Exception, match=r"national_id|身分證"):
        await svc.ensure_seller_role(store_id, contact.id, actor_user_id=clerk_id)


async def test_seller_tag_only_appears_after_an_acquisition_exists(
    db_session: AsyncSession,
) -> None:
    """在收購頁上「選好賣方」不等於「賣過東西」。

    標記賣方的唯一時機是收購**成立**（ensure_seller_role 與收購同一交易）。若在建檔或
    補登身分證字號時就先標，店員按取消、或收購中途失敗，這個人就永遠掛著賣方標記——
    帳面上他賣過東西，實際上一次都沒有（Codex 對抗式審查 高）。
    """
    store_id, _ = await _store_and_clerk(db_session)
    svc = ContactService(db_session)

    # 收購頁的「建立新賣方」：帶身分證字號建檔，但**還沒有任何收購**。
    contact = await svc.create_contact(
        store_id,
        ContactCreate(name="還沒賣", phone="0900222111", national_id="A123456789"),
    )

    assert ContactRole.SELLER.value not in contact.roles
    assert contact.roles == [ContactRole.MEMBER.value]
