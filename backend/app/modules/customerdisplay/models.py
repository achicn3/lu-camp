"""客顯裝置與 POS 櫃檯資料模型。

裝置登入憑證只保存 SHA-256；瀏覽器拿到的原始 session token 僅存在 HttpOnly cookie。
配對碼同樣只保存店別綁定後的 hash，並以 expires_at / consumed_at 表達一次性生命週期。
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class PosTerminal(Base, TimestampMixin):
    """店員操作的 POS 櫃檯瀏覽器安裝實體。"""

    __tablename__ = "pos_terminals"
    __table_args__ = (
        UniqueConstraint(
            "store_id",
            "installation_id",
            name="uq_pos_terminals_store_installation",
        ),
        UniqueConstraint("id", "store_id", name="uq_pos_terminals_id_store"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    installation_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(100))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KioskDevice(Base, TimestampMixin):
    """顧客平板的瀏覽器安裝實體；同一 KIOSK 帳號可管理多台實體裝置。"""

    __tablename__ = "kiosk_devices"
    __table_args__ = (
        UniqueConstraint(
            "kiosk_user_id",
            "installation_id",
            name="uq_kiosk_devices_user_installation",
        ),
        UniqueConstraint("id", "store_id", name="uq_kiosk_devices_id_store"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    kiosk_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    installation_id: Mapped[str] = mapped_column(String(36))
    label: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KioskDeviceSession(Base, TimestampMixin):
    """可撤銷的 KIOSK 裝置 session；token/csrf 均只落 hash。"""

    __tablename__ = "kiosk_device_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["kiosk_device_id", "store_id"],
            ["kiosk_devices.id", "kiosk_devices.store_id"],
            name="fk_kiosk_device_sessions_device_store",
        ),
        UniqueConstraint("token_hash", name="uq_kiosk_device_sessions_token_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    kiosk_device_id: Mapped[int] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    csrf_token_hash: Mapped[str] = mapped_column(String(64))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KioskPairingCode(Base, TimestampMixin):
    """短效一次性配對碼；明碼只回到該 KIOSK 畫面一次。"""

    __tablename__ = "kiosk_pairing_codes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["kiosk_device_id", "store_id"],
            ["kiosk_devices.id", "kiosk_devices.store_id"],
            name="fk_kiosk_pairing_codes_device_store",
        ),
        Index(
            "uq_kiosk_pairing_codes_store_active_hash",
            "store_id",
            "code_hash",
            unique=True,
            postgresql_where=text("consumed_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    kiosk_device_id: Mapped[int] = mapped_column(index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TerminalKioskPairing(Base, TimestampMixin):
    """櫃檯與客顯的長期配對；解除以 unpaired_at 留下歷史，不刪列。"""

    __tablename__ = "terminal_kiosk_pairings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["pos_terminal_id", "store_id"],
            ["pos_terminals.id", "pos_terminals.store_id"],
            name="fk_terminal_kiosk_pairings_terminal_store",
        ),
        ForeignKeyConstraint(
            ["kiosk_device_id", "store_id"],
            ["kiosk_devices.id", "kiosk_devices.store_id"],
            name="fk_terminal_kiosk_pairings_device_store",
        ),
        Index(
            "uq_terminal_kiosk_pairings_active_terminal",
            "pos_terminal_id",
            unique=True,
            postgresql_where=text("unpaired_at IS NULL"),
        ),
        Index(
            "uq_terminal_kiosk_pairings_active_device",
            "kiosk_device_id",
            unique=True,
            postgresql_where=text("unpaired_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    pos_terminal_id: Mapped[int] = mapped_column(index=True)
    kiosk_device_id: Mapped[int] = mapped_column(index=True)
    paired_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    paired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    unpaired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
