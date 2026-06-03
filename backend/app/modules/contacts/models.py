"""contacts 模型：統一聯絡人主檔（會員/賣方/寄售人）。

national_id 不存明文：以 national_id_enc（密文）+ national_id_blind_index（HMAC，精確去重）儲存。
roles 為陣列，支援同一主檔具備多重角色。
"""

from sqlalchemy import ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class Contact(Base, TimestampMixin):
    __tablename__ = "contacts"
    __table_args__ = (
        # 同店內以 blind index 精確去重（national_id 為空時多筆 NULL 不衝突）。
        UniqueConstraint(
            "store_id", "national_id_blind_index", name="uq_contacts_store_blind_index"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(30), index=True)
    national_id_enc: Mapped[str | None] = mapped_column(String(255))
    national_id_blind_index: Mapped[str | None] = mapped_column(String(64), index=True)
    roles: Mapped[list[str]] = mapped_column(
        ARRAY(String(20)), default=list, server_default=text("'{}'")
    )
    member_points: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    default_carrier_type: Mapped[str | None] = mapped_column(String(10))
    default_carrier_id: Mapped[str | None] = mapped_column(String(64))
    source_note: Mapped[str | None] = mapped_column(String(500))
