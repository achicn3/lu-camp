"""staff 模型：員工名單（docs/99 §4.4）。

員工不一定有系統帳號（裁示 6：工讀生可能沒有）；有的話一個帳號只能對到一位員工。
停用而不刪：待辦、排班的歷史紀錄還指著他。
"""

from sqlalchemy import ForeignKey, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin

STAFF_NAME_MAX = 50


class StaffMember(Base, TimestampMixin):
    __tablename__ = "staff_members"
    __table_args__ = (
        UniqueConstraint("store_id", "name", name="uq_staff_members_store_name"),
        # NULL 不互相衝突：沒有帳號的員工可以很多位。
        UniqueConstraint("store_id", "user_id", name="uq_staff_members_store_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(STAFF_NAME_MAX))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
