"""signing 模型：簽署任務＋切結書版本（docs/23 §4）。

`signature_tasks` 為簽署事實來源：content JSONB 為**顯示內容快照**（客人簽的就是這份），
簽名影像（PNG bytes）與時間戳落於同列；AFFIDAVIT 任務必綁 agreement 版本。
`agreement_versions` 不可變（改版＝新列），舊簽名永遠指向舊版全文。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DDL,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import PayoutMethod, SignatureTaskKind, SignatureTaskStatus


def _enum_col(enum_cls: type) -> Enum:
    return Enum(enum_cls, native_enum=False, length=30, create_constraint=True)


class AgreementVersion(Base):
    """切結書/條款版本（不可變；lazy 由 agreements.AGREEMENT_TEXTS 落庫）。"""

    __tablename__ = "agreement_versions"
    __table_args__ = (UniqueConstraint("version", name="uq_agreement_versions_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column()
    title: Mapped[str] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SignatureTask(Base, TimestampMixin):
    """簽署任務與不可變證據主檔；每次狀態轉移另寫 SignatureTaskEvent。

    - content：顯示內容快照（品項/金額/會員資料/扣抵金額等，由發起端組裝）。
    - chosen_payout：AFFIDAVIT 任務客人於手持端二選一（CASH/STORE_CREDIT，D7；SPLIT 被
      CHECK 與 service 雙重擋下），簽名送出時寫入、供 K4 收購回填。
    - signature_image：PNG bytes；SIGNED 必有簽名與時間戳（CHECK）。
    """

    __tablename__ = "signature_tasks"
    __table_args__ = (
        # 租戶配對：任務對象必屬同店（沿 storecredit 複合 FK 慣例）。
        ForeignKeyConstraint(
            ["contact_id", "store_id"],
            ["contacts.id", "contacts.store_id"],
            name="fk_signature_tasks_contact_store",
        ),
        ForeignKeyConstraint(
            ["kiosk_device_id", "store_id"],
            ["kiosk_devices.id", "kiosk_devices.store_id"],
            name="fk_signature_tasks_kiosk_device_store",
        ),
        ForeignKeyConstraint(
            ["cart_session_id", "store_id"],
            ["cart_sessions.id", "cart_sessions.store_id"],
            name="fk_signature_tasks_cart_session_store",
            use_alter=True,
        ),
        # PNG 可依保存政策獨立清除；SIGNED 仍須保留簽署時間與三組不可變 hash。
        CheckConstraint(
            "status NOT IN ('SIGNED','CONSUMED','FAILED') OR "
            "(signed_at IS NOT NULL AND signature_sha256 IS NOT NULL "
            "AND content_sha256 IS NOT NULL "
            "AND evidence_hash IS NOT NULL)",
            name="ck_signature_tasks_signed_evidence",
        ),
        # 切結書任務必綁條款版本（簽的是哪一版必可追溯）。
        CheckConstraint(
            "kind <> 'ACQUISITION_AFFIDAVIT' OR agreement_version_id IS NOT NULL",
            name="ck_signature_tasks_affidavit_agreement",
        ),
        # 撥款選擇僅限二選一（docs/23 D7）：SPLIT 在 DB 層即被擋。
        CheckConstraint(
            "chosen_payout IS NULL OR chosen_payout <> 'SPLIT'",
            name="ck_signature_tasks_payout_binary",
        ),
        # 已簽的收購切結必有撥款選擇（docs/23 K4，Codex 第三輪）：杜絕 NULL 撥款的已簽
        # 買斷切結成為可綁定的證據。
        CheckConstraint(
            "NOT (status IN ('SIGNED','CONSUMED','FAILED') "
            "AND kind = 'ACQUISITION_AFFIDAVIT') "
            "OR chosen_payout IS NOT NULL",
            name="ck_signature_tasks_signed_affidavit_payout",
        ),
        UniqueConstraint("id", "store_id", name="uq_signature_tasks_id_store"),
        Index("ix_signature_tasks_store_status", "store_id", "status"),
        Index(
            "uq_signature_tasks_active_kiosk",
            "kiosk_device_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING','SIGNING')"),
        ),
        Index(
            "uq_signature_tasks_active_cart",
            "cart_session_id",
            unique=True,
            postgresql_where=text(
                "cart_session_id IS NOT NULL AND status IN ('PENDING','SIGNING','SIGNED')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    kind: Mapped[SignatureTaskKind] = mapped_column(_enum_col(SignatureTaskKind))
    status: Mapped[SignatureTaskStatus] = mapped_column(
        _enum_col(SignatureTaskStatus),
        default=SignatureTaskStatus.PENDING,
        server_default=SignatureTaskStatus.PENDING.value,
    )
    contact_id: Mapped[int] = mapped_column(index=True)  # 複合租戶 FK 見 __table_args__
    kiosk_device_id: Mapped[int | None] = mapped_column(index=True)
    cart_session_id: Mapped[int | None] = mapped_column(index=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)  # 顯示內容快照
    agreement_version_id: Mapped[int | None] = mapped_column(ForeignKey("agreement_versions.id"))
    chosen_payout: Mapped[PayoutMethod | None] = mapped_column(_enum_col(PayoutMethod))
    signature_image: Mapped[bytes | None] = mapped_column(LargeBinary)  # PNG
    signature_sha256: Mapped[str | None] = mapped_column(String(64))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    evidence_hash: Mapped[str | None] = mapped_column(String(64))
    cart_snapshot_fingerprint: Mapped[str | None] = mapped_column(String(64))
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_user_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(300))
    signature_retention_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    signature_cleanup_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 快照、hash 與事件屬交易紀錄；和可獨立清除的簽名 PNG 分開標示保存政策。
    retention_policy: Mapped[str] = mapped_column(
        String(40),
        server_default=text("'TRANSACTION_RECORD_5Y'"),
        nullable=False,
    )
    # 簽名冪等指紋 = sha256(客端鍵 ∥ 簽名影像 ∥ 撥款)：手持端「已提交但回應遺失」時以同鍵＋
    # 同內容重送 → 後端回放同結果而非 409；同鍵但改了內容 → 指紋不同 → 409（不覆蓋既簽）。
    # 綁內容避免「遺失 CASH 回應後改送 STORE_CREDIT 拿到舊 200」（docs/23 Codex K3 第六/七輪）。
    sign_idempotency_key: Mapped[str | None] = mapped_column(String(80))
    # 綁定用穩定身分指紋 = national_id_blind_index（HMAC，非明文、非可逆），建立時凍結。
    # **伺服器內部欄、絕不列入任何讀取序列化/API 回應**（含手持端）——遮罩有損，此指紋供 K4
    # 收購綁定精確比對身分；放 content JSON 會被手持端讀到、跨越 D1/D4 PII 邊界（K4 第十一輪）。
    identity_fingerprint: Mapped[str | None] = mapped_column(String(64))
    # 關聯單據（K4/K5 回填）：acquisition/sale 等；不設 FK（跨模組鬆耦合、單據於簽後才建）。
    ref_type: Mapped[str | None] = mapped_column(String(30))
    ref_id: Mapped[int | None] = mapped_column()
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class SignatureTaskEvent(Base):
    """簽署狀態證據鏈；只允許 INSERT，原／新狀態與操作者皆由後端時間落地。"""

    __tablename__ = "signature_task_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["signature_task_id", "store_id"],
            ["signature_tasks.id", "signature_tasks.store_id"],
            name="fk_signature_task_events_task_store",
        ),
        ForeignKeyConstraint(
            ["sale_id", "store_id"],
            ["sales.id", "sales.store_id"],
            name="fk_signature_task_events_sale_store",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    signature_task_id: Mapped[int] = mapped_column(index=True)
    from_status: Mapped[SignatureTaskStatus | None] = mapped_column(
        Enum(SignatureTaskStatus, native_enum=False, length=30, create_constraint=False)
    )
    to_status: Mapped[SignatureTaskStatus] = mapped_column(
        Enum(SignatureTaskStatus, native_enum=False, length=30, create_constraint=False)
    )
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_kiosk_device_id: Mapped[int | None] = mapped_column(ForeignKey("kiosk_devices.id"))
    reason_code: Mapped[str] = mapped_column(String(60))
    reason_detail: Mapped[str | None] = mapped_column(String(300))
    cart_session_id: Mapped[int | None] = mapped_column(ForeignKey("cart_sessions.id"))
    sale_id: Mapped[int | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


SIGNATURE_TASK_EVENT_IMMUTABLE_DDL: tuple[str, ...] = (
    """
CREATE OR REPLACE FUNCTION signature_task_event_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'signature_task_events 為 insert-only：禁止 UPDATE/DELETE';
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE TRIGGER trg_signature_task_events_immutable
BEFORE UPDATE OR DELETE ON signature_task_events
FOR EACH ROW EXECUTE FUNCTION signature_task_event_immutable()
""",
)

SIGNATURE_TASK_EVENT_IMMUTABLE_DROP_DDL: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS trg_signature_task_events_immutable ON signature_task_events",
    "DROP FUNCTION IF EXISTS signature_task_event_immutable()",
)

SIGNATURE_TASK_EVIDENCE_IMMUTABLE_DDL: tuple[str, ...] = (
    """
CREATE OR REPLACE FUNCTION signature_task_evidence_immutable() RETURNS trigger AS $$
BEGIN
  IF OLD.store_id IS DISTINCT FROM NEW.store_id
     OR OLD.kind IS DISTINCT FROM NEW.kind
     OR OLD.contact_id IS DISTINCT FROM NEW.contact_id
     OR OLD.kiosk_device_id IS DISTINCT FROM NEW.kiosk_device_id
     OR OLD.cart_session_id IS DISTINCT FROM NEW.cart_session_id
     OR OLD.content IS DISTINCT FROM NEW.content
     OR OLD.agreement_version_id IS DISTINCT FROM NEW.agreement_version_id
     OR OLD.cart_snapshot_fingerprint IS DISTINCT FROM NEW.cart_snapshot_fingerprint
     OR OLD.identity_fingerprint IS DISTINCT FROM NEW.identity_fingerprint
     OR OLD.ref_type IS DISTINCT FROM NEW.ref_type
     OR OLD.ref_id IS DISTINCT FROM NEW.ref_id
     OR OLD.created_by IS DISTINCT FROM NEW.created_by
     OR OLD.retention_policy IS DISTINCT FROM NEW.retention_policy THEN
    RAISE EXCEPTION 'signature_tasks 的簽署內容與歸屬不可修改';
  END IF;
  IF OLD.signed_at IS NOT NULL THEN
    IF OLD.signed_at IS DISTINCT FROM NEW.signed_at
       OR OLD.chosen_payout IS DISTINCT FROM NEW.chosen_payout
       OR OLD.signature_sha256 IS DISTINCT FROM NEW.signature_sha256
       OR OLD.content_sha256 IS DISTINCT FROM NEW.content_sha256
       OR OLD.evidence_hash IS DISTINCT FROM NEW.evidence_hash
       OR OLD.sign_idempotency_key IS DISTINCT FROM NEW.sign_idempotency_key
       OR OLD.signature_retention_until IS DISTINCT FROM NEW.signature_retention_until THEN
      RAISE EXCEPTION 'signature_tasks 的已封存證據不可修改';
    END IF;
    IF (OLD.signature_image IS NULL AND NEW.signature_image IS NOT NULL)
       OR (OLD.signature_image IS NOT NULL AND NEW.signature_image IS NOT NULL
           AND OLD.signature_image IS DISTINCT FROM NEW.signature_image) THEN
      RAISE EXCEPTION '簽名 PNG 封存後只能依保存政策清除，不可替換或還原';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE TRIGGER trg_signature_tasks_evidence_immutable
BEFORE UPDATE ON signature_tasks
FOR EACH ROW EXECUTE FUNCTION signature_task_evidence_immutable()
""",
)

SIGNATURE_TASK_EVIDENCE_IMMUTABLE_DROP_DDL: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS trg_signature_tasks_evidence_immutable ON signature_tasks",
    "DROP FUNCTION IF EXISTS signature_task_evidence_immutable()",
)

for _ddl in SIGNATURE_TASK_EVENT_IMMUTABLE_DDL:
    event.listen(SignatureTaskEvent.__table__, "after_create", DDL(_ddl))  # type: ignore[no-untyped-call]

for _ddl in SIGNATURE_TASK_EVIDENCE_IMMUTABLE_DDL:
    event.listen(SignatureTask.__table__, "after_create", DDL(_ddl))  # type: ignore[no-untyped-call]
