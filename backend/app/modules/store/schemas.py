"""store 的 Pydantic schema：收據抬頭輸出。

收據／明細聯抬頭的單一事實來源為 `stores` 表（CLAUDE.md §4）；本 schema 形狀對齊
hardware-agent 端的 `StoreHeader`（name/tax_id/address/phone/invoice_track_info），
後端序列化後 agent 直接取用（§11 合約優先，不在 agent 手刻型別）。
"""

from pydantic import BaseModel, ConfigDict

from app.modules.store.models import Store


class ReceiptHeaderRead(BaseModel):
    """收據／明細聯抬頭（店名/統編/地址/電話/發票字軌資訊）。"""

    model_config = ConfigDict(from_attributes=True)

    name: str
    tax_id: str | None
    address: str | None
    phone: str | None
    invoice_track_info: str | None

    @classmethod
    def from_model(cls, store: Store) -> "ReceiptHeaderRead":
        return cls.model_validate(store)
