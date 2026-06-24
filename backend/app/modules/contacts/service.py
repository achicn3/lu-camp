"""contacts 業務邏輯：加密 national_id、blind-index 去重、解密查看寫稽核。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.crypto import get_pii_cipher, national_id_blind_index
from app.core.national_id import is_valid_national_id
from app.modules.contacts.models import Contact
from app.modules.contacts.repository import ContactRepository
from app.modules.contacts.schemas import ContactCreate, ContactUpdate
from app.shared.enums import ContactRole
from app.shared.exceptions import (
    AcquisitionRequiresNationalId,
    DuplicateContact,
    InvalidNationalId,
    MemberPointsAdjustFailed,
    MemberRemovalBlocked,
)


class ContactService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ContactRepository(session)

    async def create_contact(self, store_id: int, data: ContactCreate) -> Contact:
        """建檔；手機同店唯一（撞號 → DuplicateContact，請改以手機查找既有會員）；
        national_id 加密儲存，並以 blind index 精確去重（命中既有則回傳既有）。"""
        # 手機為店內唯一識別：撞號即擋，避免同一人重複建檔（DB 唯一約束為最終防線）。
        if await self._repo.get_by_phone(store_id, data.phone) is not None:
            raise DuplicateContact(
                "此手機號碼已有聯絡人，請改以手機查找既有會員後補登/編輯"
            )
        enc: str | None = None
        blind: str | None = None
        if data.national_id:
            if not is_valid_national_id(data.national_id):
                raise InvalidNationalId("身分證字號格式或檢核碼不正確，請確認後重新輸入")
            blind = national_id_blind_index(data.national_id)
            existing = await self._repo.get_by_blind_index(store_id, blind)
            if existing is not None:
                return existing
            enc = get_pii_cipher().encrypt(data.national_id)

        contact = Contact(
            store_id=store_id,
            name=data.name,
            phone=data.phone,
            national_id_enc=enc,
            national_id_blind_index=blind,
            roles=[role.value for role in data.roles],
            member_points=data.member_points,
            default_carrier_type=data.default_carrier_type,
            default_carrier_id=data.default_carrier_id,
            source_note=data.source_note,
        )
        return await self._repo.add(contact)

    async def update_contact(
        self,
        store_id: int,
        contact_id: int,
        data: ContactUpdate,
        provided: set[str],
        *,
        actor_user_id: int,
    ) -> Contact | None:
        """編輯聯絡人（docs/17 §4.3、§5.2）；回 None 表查無（含跨店）→ router 轉 404。

        `provided` 為呼叫端提供的欄位集合（PATCH 語意），只更新被提供的欄位。national_id
        變更走加密 + blind index 重算 + 同店他人去重（命中 → DuplicateContact 409）；去重的
        最終防線為 DB 複合唯一約束，並發競態下 flush 觸發 IntegrityError、由 router 整筆回滾。
        角色/national_id 的 MANAGER 權限於 router 把關（依 provided 欄位）。

        以 FOR UPDATE 鎖定該列再讀-改-驗（D-1 模式）：序列化同列的並發編輯，使
        SELLER/CONSIGNOR↔national_id 不變量於持鎖期間以最新狀態重驗，杜絕「一交易清
        national_id、另一交易加 SELLER」各憑舊快照通過的競態（Codex 對抗式審查 high）。
        """
        contact = await self._repo.get_for_update(store_id, contact_id)
        if contact is None:
            return None

        had_national_id = contact.national_id_enc is not None
        roles_before = list(contact.roles)

        if "name" in provided:
            if data.name is None:
                raise ValueError("姓名不可為空")
            contact.name = data.name
        if "phone" in provided:
            if not data.phone:
                raise ValueError("電話不可為空")
            if data.phone != contact.phone:
                clash = await self._repo.get_by_phone(store_id, data.phone)
                if clash is not None and clash.id != contact.id:
                    raise DuplicateContact("此手機號碼已被同店其他聯絡人使用")
            contact.phone = data.phone
        if "default_carrier_type" in provided:
            contact.default_carrier_type = data.default_carrier_type
        if "default_carrier_id" in provided:
            contact.default_carrier_id = data.default_carrier_id
        if "source_note" in provided:
            contact.source_note = data.source_note
        if "roles" in provided:
            contact.roles = [role.value for role in (data.roles or [])]
            await self._guard_member_removal(store_id, contact_id, roles_before, contact.roles)
        if "national_id" in provided:
            await self._apply_national_id_change(store_id, contact, data.national_id)

        # 收購/寄售角色必須有 national_id（沿 ContactCreate 不變量，CLAUDE.md §5）。
        needs_id = {ContactRole.SELLER.value, ContactRole.CONSIGNOR.value} & set(contact.roles)
        if needs_id and contact.national_id_enc is None:
            raise AcquisitionRequiresNationalId(
                "收購/寄售對象（SELLER/CONSIGNOR）必須有 national_id"
            )

        await self._repo.save(contact)

        # 稽核：PII / 角色 / 一般欄位分別留痕（PII 不含明文，由 audit 遮罩 + 僅記旗標）。
        if "national_id" in provided:
            await write_audit_log(
                self._session,
                store_id=store_id,
                actor_user_id=actor_user_id,
                action="UPDATE_CONTACT_PII",
                entity_type="contact",
                entity_id=str(contact_id),
                before={"had_national_id": had_national_id},
                after={"has_national_id": contact.national_id_enc is not None},
                is_sensitive=True,
            )
        if "roles" in provided:
            await write_audit_log(
                self._session,
                store_id=store_id,
                actor_user_id=actor_user_id,
                action="UPDATE_CONTACT_ROLES",
                entity_type="contact",
                entity_id=str(contact_id),
                before={"roles": roles_before},
                after={"roles": list(contact.roles)},
            )
        general = {"name", "phone", "default_carrier_type", "default_carrier_id", "source_note"}
        changed_general = sorted(general & provided)
        if changed_general:
            await write_audit_log(
                self._session,
                store_id=store_id,
                actor_user_id=actor_user_id,
                action="UPDATE_CONTACT",
                entity_type="contact",
                entity_id=str(contact_id),
                after={"fields": changed_general},
            )
        return contact

    async def _guard_member_removal(
        self, store_id: int, contact_id: int, roles_before: list[str], roles_after: list[str]
    ) -> None:
        """移除 MEMBER 角色前，確認 contact 未持有購物金帳戶/帳本（裁示 #3、Codex high）。

        否則會留下「非會員仍掛購物金負債」、破壞 storecredit 的會員邊界（I-8）並使報表
        錯分類。以對方 service 唯讀查詢（facade，跨模組只經 service）。SELLER/CONSIGNOR 移除
        不受此限（其關聯為 contact_id 直接 FK、非角色閘）。
        """
        removed_member = (
            ContactRole.MEMBER.value in roles_before and ContactRole.MEMBER.value not in roles_after
        )
        if not removed_member:
            return
        # 函式內 import 打破 contacts↔storecredit 循環相依（CLAUDE.md §9 允許之唯一例外）。
        from app.modules.storecredit.service import StoreCreditService

        if await StoreCreditService(self._session).has_store_credit(store_id, contact_id):
            raise MemberRemovalBlocked(
                f"contact {contact_id} 仍持有購物金帳戶/帳本，不可移除 MEMBER 角色（I-8）"
            )

    async def _apply_national_id_change(
        self, store_id: int, contact: Contact, new_national_id: str | None
    ) -> None:
        """設定/清空 national_id：加密 + 重算 blind index + 同店他人去重（原子，同一交易內）。"""
        # 空白/全空白視為「無 national_id」（清空），與 ContactCreate 的 falsy 處理一致：
        # 否則可用空字串讓 national_id_enc 非 None、偽裝 has_national_id 並繞過 SELLER/
        # CONSIGNOR 必填不變量（Codex 對抗式審查 high）。非空白值維持原樣（與 create 同一
        # 正規形，確保跨 create/update 的 blind index 去重一致）。
        if new_national_id is None or not new_national_id.strip():
            contact.national_id_enc = None
            contact.national_id_blind_index = None
            return
        if not is_valid_national_id(new_national_id):
            raise InvalidNationalId("身分證字號格式或檢核碼不正確，請確認後重新輸入")
        blind = national_id_blind_index(new_national_id)
        existing = await self._repo.get_by_blind_index(store_id, blind)
        if existing is not None and existing.id != contact.id:
            raise DuplicateContact(
                f"national_id 已被同店其他聯絡人（{existing.id}）使用，不可重複建檔"
            )
        contact.national_id_enc = get_pii_cipher().encrypt(new_national_id)
        contact.national_id_blind_index = blind

    async def add_member_points(self, store_id: int, contact_id: int, delta: int) -> None:
        """調整會員點數（結帳累積 +、作廢沖回 −；docs/16 §0）。

        原子 UPDATE（不讀-改-寫）；對象不存在/跨店/將為負 → MemberPointsAdjustFailed，
        由呼叫端的交易整筆回滾（點數與銷售同生共死）。
        """
        if delta == 0:
            return
        if not await self._repo.adjust_member_points(store_id, contact_id, delta):
            raise MemberPointsAdjustFailed(
                f"contact {contact_id} 點數調整 {delta:+d} 失敗（不存在/跨店/將為負）"
            )

    async def get_contact(self, store_id: int, contact_id: int) -> Contact | None:
        return await self._repo.get(store_id, contact_id)

    async def get_contact_for_update(self, store_id: int, contact_id: int) -> Contact | None:
        """SELECT … FOR UPDATE 鎖定 contact 列再讀（D-1 模式）。

        供 storecredit 入帳/校正在驗會員資格前鎖定該列，與 contacts 的 MEMBER 移除
        守衛在**同一列**互斥，關閉「移除 MEMBER ⇄ 並發首筆入帳」競態（Codex 對抗式
        審查 high）。鎖序固定 contact→account，與既有帳戶鎖無循環、不致死鎖。
        """
        return await self._repo.get_for_update(store_id, contact_id)

    async def lookup_by_national_id(self, store_id: int, national_id: str) -> Contact | None:
        """以 blind index 精確比對既有聯絡人（供收購去重）。"""
        return await self._repo.get_by_blind_index(store_id, national_id_blind_index(national_id))

    async def search(
        self, store_id: int, role: str | None, q: str | None, *, limit: int, offset: int
    ) -> list[Contact]:
        return await self._repo.search(store_id, role, q, limit=limit, offset=offset)

    async def reveal_national_id(
        self, store_id: int, contact_id: int, actor_user_id: int
    ) -> str | None:
        """解密 national_id 並寫稽核（稽核本身不含明文）。"""
        contact = await self._repo.get(store_id, contact_id)
        if contact is None or contact.national_id_enc is None:
            return None
        plaintext = get_pii_cipher().decrypt(contact.national_id_enc)
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="VIEW_NATIONAL_ID",
            entity_type="contact",
            entity_id=str(contact_id),
            is_sensitive=True,
        )
        return plaintext
