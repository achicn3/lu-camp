"""contacts 業務邏輯：加密 national_id、blind-index 去重、解密查看寫稽核。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.crypto import get_pii_cipher, national_id_blind_index
from app.modules.contacts.models import Contact
from app.modules.contacts.repository import ContactRepository
from app.modules.contacts.schemas import ContactCreate
from app.shared.exceptions import MemberPointsAdjustFailed


class ContactService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ContactRepository(session)

    async def create_contact(self, store_id: int, data: ContactCreate) -> Contact:
        """建檔；national_id 加密儲存，並以 blind index 精確去重（命中既有則回傳既有）。"""
        enc: str | None = None
        blind: str | None = None
        if data.national_id:
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
