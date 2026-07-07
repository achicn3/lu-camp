"""signing Pydantic schemas：店務端（建立/查詢/作廢）與手持端（輪詢/簽名送出）。

content 為顯示內容快照（發起端組裝；PII 遮罩規則由發起端負責，D1：姓名全顯、證號遮罩）。
簽名影像以 base64 PNG 傳輸；讀取端點僅回 has_signature 旗標，影像另走專用端點取 bytes。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.shared.enums import PayoutMethod, SignatureTaskKind, SignatureTaskStatus

MAX_SIGNATURE_BYTES = 512_000  # 手寫簽名 PNG 綽綽有餘；擋整頁截圖/照片級 payload
# base64 膨脹 4/3；schema 先擋（422），服務層解碼前再驗一次（最後防線）
MAX_SIGNATURE_B64_CHARS = MAX_SIGNATURE_BYTES * 4 // 3 + 8


class SignatureTaskCreate(BaseModel):
    kind: SignatureTaskKind
    contact_id: int
    content: dict[str, Any]
    ref_type: str | None = Field(default=None, max_length=30)
    ref_id: int | None = None


class SignatureTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    kind: SignatureTaskKind
    status: SignatureTaskStatus
    contact_id: int
    content: dict[str, Any]
    agreement_version: int | None
    chosen_payout: PayoutMethod | None
    has_signature: bool
    signed_at: datetime | None
    cancelled_at: datetime | None
    ref_type: str | None
    ref_id: int | None
    created_at: datetime


class KioskTaskRead(SignatureTaskRead):
    """手持端任務視圖：AFFIDAVIT 任務附上切結書全文（客人簽的就是這份）。"""

    agreement_title: str | None
    agreement_body: str | None


class KioskSignRequest(BaseModel):
    signature_image_base64: str = Field(min_length=1, max_length=MAX_SIGNATURE_B64_CHARS)
    # AFFIDAVIT 必填且限 CASH/STORE_CREDIT（D7 二選一）；其他任務種類必須不帶。
    chosen_payout: PayoutMethod | None = None
    # 客端每張任務一鍵：回應遺失時以同鍵重送安全回放（idempotent；Codex K3 第六輪）。
    idempotency_key: str | None = Field(default=None, max_length=80)
