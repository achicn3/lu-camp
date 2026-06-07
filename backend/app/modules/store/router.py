"""store 路由：收據抬頭讀取。

`GET /stores/{store_id}/receipt-header` 提供收據／明細聯抬頭（店名/統編/地址/電話/
發票字軌資訊）給 hardware-agent 列印用。

**刻意不設認證**（產品裁示 2026-06-07）：抬頭是印在每張收據上的公開、非 PII 資訊，
呼叫端 hardware-agent 為 localhost 服務、非登入使用者。此端點僅唯讀、不回任何敏感
個資，故不掛 `get_current_user`；若日後改為需認證，於此加依賴即可。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.store.schemas import ReceiptHeaderRead
from app.modules.store.service import StoreService
from app.shared.exceptions import StoreNotFound

router = APIRouter(prefix="/stores", tags=["stores"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get(
    "/{store_id}/receipt-header",
    response_model=ReceiptHeaderRead,
    operation_id="getStoreReceiptHeader",
)
async def get_receipt_header(store_id: int, session: SessionDep) -> ReceiptHeaderRead:
    """回傳指定門市的收據抬頭；門市不存在 → 404。"""
    try:
        store = await StoreService(session).get_receipt_header(store_id)
    except StoreNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到門市") from exc
    return ReceiptHeaderRead.from_model(store)
