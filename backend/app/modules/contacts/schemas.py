"""contacts 的 Pydantic schema：輸入驗證與遮罩輸出（預設不回 national_id 明文）。"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.contacts.models import Contact
from app.shared.enums import ContactRole

_MASK = "***"


class ContactCreate(BaseModel):
    """建立聯絡人輸入。手機必填、同店唯一（供以手機查找既有會員、避免重複建檔）；
    收購/寄售對象（SELLER/CONSIGNOR）另必填 national_id。"""

    name: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    national_id: str | None = None
    address: str | None = Field(default=None, max_length=200)  # K1：住址（明文，D5）
    roles: list[ContactRole] = Field(default_factory=list)
    member_points: int = Field(default=0, ge=0)  # 點數不可為負（docs/16 §0 僅累積）
    default_carrier_type: str | None = None
    default_carrier_id: str | None = None
    source_note: str | None = None

    @model_validator(mode="after")
    def _require_national_id_for_acquisition(self) -> "ContactCreate":
        needs_id = {ContactRole.SELLER, ContactRole.CONSIGNOR} & set(self.roles)
        if needs_id and not self.national_id:
            raise ValueError("收購/寄售對象（SELLER/CONSIGNOR）必須提供 national_id")
        return self


class ContactUpdate(BaseModel):
    """編輯聯絡人（PATCH 語意；docs/17 §5.2、裁示 #3）。

    所有欄位皆為選配；以 `model_fields_set` 區分「未提供」與「明確設為 null」。
    角色/national_id 變更的 RBAC（限 MANAGER）由 router 依提供欄位判定。
    member_points 不在此編輯（走點數累積/校正路徑）。
    """

    name: str | None = Field(default=None, min_length=1)
    phone: str | None = None
    national_id: str | None = None
    address: str | None = Field(default=None, max_length=200)  # 可改可清（PATCH 語意）
    roles: list[ContactRole] | None = None
    default_carrier_type: str | None = None
    default_carrier_id: str | None = None
    source_note: str | None = None


class ContactRead(BaseModel):
    """聯絡人輸出：national_id 一律遮罩，不回明文。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    name: str
    phone: str | None
    address: str | None
    roles: list[str]
    member_points: int
    default_carrier_type: str | None
    default_carrier_id: str | None
    source_note: str | None
    national_id_masked: str | None
    has_national_id: bool

    @classmethod
    def from_model(cls, contact: Contact) -> "ContactRead":
        has_id = contact.national_id_enc is not None
        return cls(
            id=contact.id,
            store_id=contact.store_id,
            name=contact.name,
            phone=contact.phone,
            address=contact.address,
            roles=list(contact.roles),
            member_points=contact.member_points,
            default_carrier_type=contact.default_carrier_type,
            default_carrier_id=contact.default_carrier_id,
            source_note=contact.source_note,
            national_id_masked=_MASK if has_id else None,
            has_national_id=has_id,
        )


class MemberWithCreditRead(BaseModel):
    """會員清單列：基本資料 + 點數 + 購物金餘額（整數元字串）。national_id 一律遮罩。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str | None
    roles: list[str]
    member_points: int
    has_national_id: bool
    store_credit_balance: str

    @classmethod
    def from_model(cls, contact: Contact, balance: Decimal) -> "MemberWithCreditRead":
        return cls(
            id=contact.id,
            name=contact.name,
            phone=contact.phone,
            roles=list(contact.roles),
            member_points=contact.member_points,
            has_national_id=contact.national_id_enc is not None,
            store_credit_balance=str(balance),
        )


class ContactLookupRequest(BaseModel):
    """以 national_id 精確查重（放 body，避免 national_id 進入 URL / access log）。"""

    national_id: str = Field(min_length=1)


class ContactNationalIdRead(BaseModel):
    """MANAGER 解密查看的回應（明文，僅此端點回傳）。"""

    national_id: str
