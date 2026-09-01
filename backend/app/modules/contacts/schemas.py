"""contacts 的 Pydantic schema：輸入驗證與遮罩輸出（預設不回 national_id 明文）。"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.money import format_ntd
from app.modules.contacts.models import Contact
from app.shared.enums import ContactRole

_MASK = "***"


class ContactCreate(BaseModel):
    """建立聯絡人輸入。手機必填、同店唯一（供以手機查找既有會員、避免重複建檔）。

    **不問角色**：每個人建檔就是會員，賣東西時由收購流程自動補上 SELLER。
    身分證字號只有 SELLER 需要——純消費的客人佔多數（一年模擬 3081 人中 2204 人
    從沒賣過東西），為了集點就要他們留身分證字號，個資責任與辦卡阻力都不划算。
    """

    name: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    national_id: str | None = None
    address: str | None = Field(default=None, max_length=200)  # K1：住址（明文，D5）
    roles: list[ContactRole] = Field(default_factory=lambda: [ContactRole.MEMBER])
    member_points: int = Field(default=0, ge=0)  # 點數不可為負（docs/16 §0 僅累積）
    default_carrier_type: str | None = None
    default_carrier_id: str | None = None
    source_note: str | None = None

    @model_validator(mode="after")
    def _member_always_and_seller_needs_national_id(self) -> "ContactCreate":
        """每個人都是會員；賣方必須有身分證字號（防贓物登記，CLAUDE.md §5）。"""
        if ContactRole.MEMBER not in self.roles:
            self.roles = [ContactRole.MEMBER, *self.roles]
        if ContactRole.SELLER in self.roles and not self.national_id:
            raise ValueError("收購對象（賣方）必須提供身分證字號")
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
    # 這裡**刻意不強制補回 MEMBER**（建檔時才強制）。曾試著在 PATCH 也補，結果是
    # `_guard_member_removal` 與 `StoreCreditMemberRequired` 永遠觸發不到——那兩道是
    # 先前對抗式審查特別要求的競態防線（移除會員 ⇄ 並發首筆購物金入帳）。為了概念整齊
    # 拆掉有測試守著的安全機制並不划算；「每個人都是會員」由建檔與 migration 保證，
    # 而危險的情況（持有購物金者）本來就擋得住。前端也已無改身分的入口。
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
            store_credit_balance=format_ntd(balance),
        )


class ContactLookupRequest(BaseModel):
    """以 national_id 精確查重（放 body，避免 national_id 進入 URL / access log）。"""

    national_id: str = Field(min_length=1)


class ContactNationalIdRead(BaseModel):
    """MANAGER 解密查看的回應（明文，僅此端點回傳）。"""

    national_id: str
