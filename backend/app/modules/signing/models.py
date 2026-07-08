"""signing 模型：簽署任務＋切結書版本（docs/23 §4）。

`signature_tasks` 為簽署事實來源：content JSONB 為**顯示內容快照**（客人簽的就是這份），
簽名影像（PNG bytes）與時間戳落於同列；AFFIDAVIT 任務必綁 agreement 版本。
`agreement_versions` 不可變（改版＝新列），舊簽名永遠指向舊版全文。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
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
    """簽署任務。PENDING → SIGNED / CANCELLED（無自動過期；過時由店員作廢）。

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
        # SIGNED 必有簽名影像與簽署時間（狀態與證據一致）。
        CheckConstraint(
            "status <> 'SIGNED' OR (signature_image IS NOT NULL AND signed_at IS NOT NULL)",
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
            "NOT (status = 'SIGNED' AND kind = 'ACQUISITION_AFFIDAVIT') "
            "OR chosen_payout IS NOT NULL",
            name="ck_signature_tasks_signed_affidavit_payout",
        ),
        Index("ix_signature_tasks_store_status", "store_id", "status"),
        # 同店同時最多一件待簽（重推＝舊單作廢的最終防線；併發建立時第二筆撞索引）。
        Index(
            "uq_signature_tasks_store_pending",
            "store_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
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
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)  # 顯示內容快照
    agreement_version_id: Mapped[int | None] = mapped_column(ForeignKey("agreement_versions.id"))
    chosen_payout: Mapped[PayoutMethod | None] = mapped_column(_enum_col(PayoutMethod))
    signature_image: Mapped[bytes | None] = mapped_column(LargeBinary)  # PNG
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
