"""列印路由（T15）：收據聯／商品明細聯／電子發票。

收據與明細聯都會先取店家抬頭（後端 `stores`，見 `agent.store_client`）再交給注入的
`ReceiptPrinter` 列印——**印出店名/統編/地址/電話**。抬頭取不到 → 503（不印無抬頭收據）。
裝置層失敗（離線/缺紙/上蓋）由 `agent.main` 的 DeviceError handler 轉對應 HTTP。
電子發票端點列印證明聯（附件一格式一），取號資料由 payload 提供（後端發票模組
T13/T14 接手後為正式來源）；AES 金鑰缺漏由 MissingDeviceConfigError handler 轉 503。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException

from agent.deps import DevicesDep, OkResponse
from agent.interfaces import InvoicePayload, SalePayload, StoreHeader
from agent.store_client import StoreHeaderClient, StoreHeaderUnavailable, build_store_header_client


@lru_cache
def _client_singleton() -> StoreHeaderClient:
    return build_store_header_client()


async def get_store_header_client() -> StoreHeaderClient:
    """注入店家抬頭 client（預設由環境變數建立、單例快取）；測試可覆寫此依賴。"""
    return _client_singleton()


StoreClientDep = Annotated[StoreHeaderClient, Depends(get_store_header_client)]

router = APIRouter(prefix="/print", tags=["print"])


async def _fetch_header(client: StoreHeaderClient, store_id: int) -> StoreHeader:
    try:
        return await client.get_header(store_id)
    except StoreHeaderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/receipt", response_model=OkResponse, operation_id="printReceipt")
async def print_receipt(
    sale: SalePayload, devices: DevicesDep, client: StoreClientDep
) -> OkResponse:
    header = await _fetch_header(client, sale.store_id)
    # 真機列印是同步阻塞 I/O（網路/逾時），卸載到 worker thread，勿阻塞事件迴圈。
    await anyio.to_thread.run_sync(devices.receipt_printer.print_receipt, sale, header)
    return OkResponse(status="ok")


@router.post("/detail", response_model=OkResponse, operation_id="printDetail")
async def print_detail(
    sale: SalePayload, devices: DevicesDep, client: StoreClientDep
) -> OkResponse:
    header = await _fetch_header(client, sale.store_id)
    await anyio.to_thread.run_sync(devices.receipt_printer.print_detail, sale, header)
    return OkResponse(status="ok")


@router.post("/einvoice", response_model=OkResponse, operation_id="printEinvoice")
async def print_einvoice(invoice: InvoicePayload, devices: DevicesDep) -> OkResponse:
    """列印電子發票證明聯（附件一格式一 + 條碼規格 v1.9，欄位見 `InvoicePayload`）。"""
    await anyio.to_thread.run_sync(devices.receipt_printer.print_einvoice, invoice)
    return OkResponse(status="ok")
