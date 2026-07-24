"""客顯裝置與 POS 櫃檯資料模型。

裝置登入憑證只保存 SHA-256；瀏覽器拿到的原始 session token 僅存在 HttpOnly cookie。
配對碼同樣只保存店別綁定後的 hash，並以 expires_at / consumed_at 表達一次性生命週期。
"""

from datetime import datetime

from sqlalchemy import (
    DDL,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import CartSessionStatus


def _enum_col(enum_cls: type) -> Enum:
    return Enum(enum_cls, native_enum=False, length=30, create_constraint=True)


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
        ForeignKeyConstraint(
            ["displayed_cart_session_id", "store_id"],
            ["cart_sessions.id", "cart_sessions.store_id"],
            name="fk_kiosk_devices_displayed_cart_store",
            use_alter=True,
        ),
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
    displayed_cart_session_id: Mapped[int | None] = mapped_column(index=True)
    displayed_revision: Mapped[int] = mapped_column(
        default=0,
        server_default=text("0"),
        nullable=False,
    )


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


class CartSession(Base, TimestampMixin):
    """伺服器權威的即時購物車草稿；客顯只渲染 snapshot，不自行算金額。"""

    __tablename__ = "cart_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["pos_terminal_id", "store_id"],
            ["pos_terminals.id", "pos_terminals.store_id"],
            name="fk_cart_sessions_terminal_store",
        ),
        ForeignKeyConstraint(
            ["kiosk_device_id", "store_id"],
            ["kiosk_devices.id", "kiosk_devices.store_id"],
            name="fk_cart_sessions_device_store",
        ),
        ForeignKeyConstraint(
            ["buyer_contact_id", "store_id"],
            ["contacts.id", "contacts.store_id"],
            name="fk_cart_sessions_buyer_store",
        ),
        ForeignKeyConstraint(
            ["active_signature_task_id", "store_id"],
            ["signature_tasks.id", "signature_tasks.store_id"],
            name="fk_cart_sessions_active_signature_task_store",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["sale_id", "store_id"],
            ["sales.id", "sales.store_id"],
            name="fk_cart_sessions_sale_store",
        ),
        UniqueConstraint("id", "store_id", name="uq_cart_sessions_id_store"),
        UniqueConstraint("sale_id", name="uq_cart_sessions_sale_id"),
        Index(
            "uq_cart_sessions_active_terminal",
            "pos_terminal_id",
            unique=True,
            postgresql_where=text("status IN ('DRAFT','FROZEN','PROCESSING','PAYMENT_UNCERTAIN')"),
        ),
        Index(
            "uq_cart_sessions_active_device",
            "kiosk_device_id",
            unique=True,
            postgresql_where=text("status IN ('DRAFT','FROZEN','PROCESSING','PAYMENT_UNCERTAIN')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    pos_terminal_id: Mapped[int] = mapped_column(index=True)
    kiosk_device_id: Mapped[int] = mapped_column(index=True)
    status: Mapped[CartSessionStatus] = mapped_column(
        _enum_col(CartSessionStatus),
        default=CartSessionStatus.DRAFT,
        server_default=CartSessionStatus.DRAFT.value,
    )
    revision: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    buyer_contact_id: Mapped[int | None] = mapped_column(index=True)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB)
    snapshot_fingerprint: Mapped[str] = mapped_column(String(64))
    last_changes: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sale_id: Mapped[int | None] = mapped_column(index=True)
    payment_order_id: Mapped[str | None] = mapped_column(String(80), index=True)
    payment_uncertain_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_uncertain_reason: Mapped[str | None] = mapped_column(String(300))
    # 外部付款已送出但結果不明時，保存可補成立本機銷售的權威請求；LINE Pay 一次性碼必先剝除。
    # 成功補單或確認失敗後立即清除。
    payment_checkout_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    active_signature_task_id: Mapped[int | None] = mapped_column(index=True)


class CartSessionEvent(Base):
    """購物車版本事件；append-only，供 SSE/稽核重讀，禁止更新或刪除。"""

    __tablename__ = "cart_session_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["cart_session_id", "store_id"],
            ["cart_sessions.id", "cart_sessions.store_id"],
            name="fk_cart_session_events_session_store",
        ),
        UniqueConstraint(
            "cart_session_id",
            "revision",
            name="uq_cart_session_events_session_revision",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    cart_session_id: Mapped[int] = mapped_column(index=True)
    revision: Mapped[int] = mapped_column()
    event_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


CART_SESSION_EVENT_IMMUTABLE_DDL: tuple[str, ...] = (
    """
CREATE OR REPLACE FUNCTION cart_session_event_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'cart_session_events 為 insert-only：禁止 UPDATE/DELETE';
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE TRIGGER trg_cart_session_events_immutable
BEFORE UPDATE OR DELETE ON cart_session_events
FOR EACH ROW EXECUTE FUNCTION cart_session_event_immutable()
""",
)

CART_SESSION_EVENT_IMMUTABLE_DROP_DDL: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS trg_cart_session_events_immutable ON cart_session_events",
    "DROP FUNCTION IF EXISTS cart_session_event_immutable()",
)

for _ddl in CART_SESSION_EVENT_IMMUTABLE_DDL:
    # sqlalchemy.DDL 無完整型別標註；這裡只忽略第三方 stub 缺口。
    event.listen(CartSessionEvent.__table__, "after_create", DDL(_ddl))  # type: ignore[no-untyped-call]
