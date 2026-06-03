"""user 模型：門市使用者（帶 store_id，多分店就緒）。"""

from sqlalchemy import Enum, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import UserRole


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # native_enum=False + create_constraint → 存成 VARCHAR + CHECK：DB 層驗證列舉值，
    # 且無 PG ENUM 型別，downgrade 時 CHECK 隨表一起移除、乾淨回復。
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, native_enum=False, length=20, create_constraint=True)
    )
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
