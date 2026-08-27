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
from agent.interfaces import (
    AcquisitionReceiptPayload,
    InvoicePayload,
    KitchenTicketPayload,
    RawPrintPayload,
    SalePayload,
    StoreHeader,
)
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


@router.post("/acquisition", response_model=OkResponse, operation_id="printAcquisitionReceipt")
async def print_acquisition(
    receipt: AcquisitionReceiptPayload, devices: DevicesDep, client: StoreClientDep
) -> OkResponse:
    """列印收購憑證聯（docs/23 K6）：切結品項/總額/撥款＋賣方簽名影像（存證）。"""
    header = await _fetch_header(client, receipt.store_id)
    await anyio.to_thread.run_sync(devices.receipt_printer.print_acquisition, receipt, header)
    return OkResponse(status="ok")


@router.post("/kitchen", response_model=OkResponse, operation_id="printKitchenTicket")
async def print_kitchen(ticket: KitchenTicketPayload, devices: DevicesDep) -> OkResponse:
    """列印出餐單（docs/35）：桌號＋餐飲品項，給吧台核對出餐。

    **不取店家抬頭**——內部作業單不需要店名/統編，也不該因後端 `stores` 取不到就印不出來。
    """
    # 出餐機接了就印那台、沒接退回收據機（解析在 AgentDevices.kitchen_ticket_printer）。
    # **缺紙不得改印櫃檯那台**——店員會以為廚房收到了，餐永遠不會被做。
    await anyio.to_thread.run_sync(devices.kitchen_ticket_printer.print_kitchen_ticket, ticket)
    return OkResponse(status="ok")


@router.post("/raw", response_model=OkResponse, operation_id="printRaw")
async def print_raw(payload: RawPrintPayload, devices: DevicesDep) -> OkResponse:
    """把外部服務產生的 ESC/POS **原樣**送到收據機。

    目前唯一的用途是 Amego 的發票補印（`/json/invoice_print`）：證明聯的二維條碼含
    一段以財政部金鑰加密的驗證資訊，**本地算不出來**，只能由加值中心產生整張版面。
    我們不解讀也不改寫它的位元組——任何加工都可能讓條碼掃不出來。

    去向與 `/print/einvoice` 相同（發票機接了就那台）——補印的是發票，不是收據。
    """
    await anyio.to_thread.run_sync(devices.einvoice_printer.print_raw, payload.decoded())
    return OkResponse(status="ok")


@router.post("/einvoice", response_model=OkResponse, operation_id="printEinvoice")
async def print_einvoice(invoice: InvoicePayload, devices: DevicesDep) -> OkResponse:
    """列印電子發票證明聯（附件一格式一 + 條碼規格 v1.9，欄位見 `InvoicePayload`）。

    發票機接了就印那台（解析在 `AgentDevices.einvoice_printer`，ADR-018）。
    **缺紙不得改印收據機**——證明聯有兌獎聯性質，店員會以為印好了而客人手上什麼都沒有。
    """
    await anyio.to_thread.run_sync(devices.einvoice_printer.print_einvoice, invoice)
    return OkResponse(status="ok")
