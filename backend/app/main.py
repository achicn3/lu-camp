"""FastAPI app factory 與 router 掛載（Phase 0 骨架）。

目前僅提供 `/health` 端點，作為防呆地基的最小可驗證端點，並讓
OpenAPI 合約管線（docs/11）有實際內容可匯出。後續模組依
docs/05-project-structure.md 掛載於此。
"""

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.modules.acquisition.router import router as acquisition_router
from app.modules.backup.router import router as backup_router
from app.modules.backup.scheduler import (
    reconcile_orphaned_jobs_on_startup,
    scheduler_loop,
    sweep_container_plaintext_on_startup,
)
from app.modules.callticket.router import router as call_ticket_router
from app.modules.campaigns.router import router as campaigns_router
from app.modules.cashdrawer.router import router as cashdrawer_router
from app.modules.consignment.router import router as consignment_router
from app.modules.contacts.router import router as contacts_router
from app.modules.customerdisplay.router import kiosk_router as customer_display_kiosk_router
from app.modules.customerdisplay.router import staff_router as customer_display_staff_router
from app.modules.customerdisplay.scheduler import scheduler_loop as customer_display_scheduler_loop
from app.modules.einvoice.router import invoices_router as einvoice_invoices_router
from app.modules.einvoice.router import router as einvoice_router
from app.modules.einvoice.scheduler import scheduler_loop as einvoice_scheduler_loop
from app.modules.intake.router import router as intake_router
from app.modules.inventory.basket_router import router as bulk_basket_router
from app.modules.inventory.router import router as inventory_router
from app.modules.menu.photos import MAX_UPLOAD_BYTES
from app.modules.menu.router import entries_router as menu_entries_router
from app.modules.menu.router import router as menu_router
from app.modules.onlineorder.orders_router import router as online_orders_router
from app.modules.onlineorder.router import router as online_order_router
from app.modules.onlineorder.scheduler import scheduler_loop as online_order_scheduler_loop
from app.modules.openingcheck.router import router as opening_check_router
from app.modules.purchasing.router import router as purchasing_router
from app.modules.reports.finance_router import router as reports_finance_router
from app.modules.reports.router import router as reports_router
from app.modules.returns.router import router as returns_router
from app.modules.sales.reasons_router import router as sales_reasons_router
from app.modules.sales.router import router as sales_router
from app.modules.settings.router import router as settings_router
from app.modules.signing.router import agreements_router as signing_agreements_router
from app.modules.signing.router import kiosk_router as signing_kiosk_router
from app.modules.signing.router import staff_router as signing_staff_router
from app.modules.stocktake.router import router as stocktake_router
from app.modules.store.router import router as store_router
from app.modules.storecredit.router import router as storecredit_router
from app.modules.storecredit.router import store_router as storecredit_store_router
from app.modules.user.router import router as auth_router
from app.shared.http import ERROR_CODE_HEADER

API_PREFIX = "/api/v1"
# 手持端請求體上限：簽名 base64（≈683KB）＋ JSON 外殼的寬裕值。手持裝置在客人手上，
# 超大 payload 於 JSON 解析「前」即以 Content-Length 擋下（服務層另有解碼前防線）。
KIOSK_MAX_BODY_BYTES = 1_000_000
# 切結書內文上限 20000 字（UTF-8 中文最多 3 bytes/字）＋標題與 JSON 外殼，抓 256KB 綽綽有餘。
# 字數上限是在 JSON 解析**之後**才驗的，擋不住有人先塞一個超大 body 進來。
AGREEMENT_MAX_BODY_BYTES = 256_000
# 菜單照片上傳（docs/44 §3.4）：檔案上限 10 MB＋multipart 外殼。multipart 會在 handler 檢查大小、
# 甚至在驗登入之前就整包解析並落暫存檔，所以一定要在這裡先擋（Codex 對抗審查 O1d）。
PHOTO_MAX_BODY_BYTES = MAX_UPLOAD_BYTES + 64 * 1024
_PHOTO_UPLOAD_PATH = re.compile(rf"^{API_PREFIX}/menu-items/[^/]+/photo/?$")


logger = logging.getLogger(__name__)


class PhotoBodyLimit:
    """照片上傳實際收到的位元組數上限（Codex 對抗審查 O1d 第二輪）。

    Content-Length 只是對方自己宣告的；這裡邊收邊數，超過就當成連線中斷讓解析停下，
    再自己回 413。只套在照片上傳路徑，其他請求原封不動。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and _PHOTO_UPLOAD_PATH.match(scope["path"])
        ):
            await self.app(scope, receive, send)
            return
        received = 0
        exceeded = False
        started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > PHOTO_MAX_BODY_BYTES:
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if exceeded:
                return  # 超量後 app 的任何回應都不送，改由下面統一回 413
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not started:
            await _photo_too_large()(scope, receive, send)


def _reject_photo_body(content_length: str | None) -> JSONResponse | None:
    """照片上傳必須帶 Content-Length（瀏覽器送 FormData 一定會帶），且不得超過上限。"""
    if content_length is None:
        return JSONResponse(status_code=411, content={"detail": "上傳照片必須帶 Content-Length"})
    try:
        too_large = int(content_length) > PHOTO_MAX_BODY_BYTES
    except ValueError:
        too_large = True
    return _photo_too_large() if too_large else None


def _photo_too_large() -> JSONResponse:
    return JSONResponse(status_code=413, content={"detail": "照片超過 10 MB，請先縮小再上傳"})


class HealthResponse(BaseModel):
    """`/health` 回應。"""

    status: str


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """啟動時起備份排程背景 tick（docs/31 §3）,關閉時優雅停止。

    tick 為到期驅動、與請求脈絡無關;主開關 backup_scheduler_enabled=false 時直接返回。
    """
    # 開機先回收上次行程遺留的 RUNNING 備份/還原（崩潰/部署孤兒）→ FAILED，避免 UI 永遠輪詢。
    with contextlib.suppress(Exception):  # 回收失敗不擋啟動（DB 未就緒等）
        await reconcile_orphaned_jobs_on_startup()
    # 掃除容器內殘留的整庫明文 dump（崩潰/中斷留下的 PII 不長期滯留）。
    await sweep_container_plaintext_on_startup()
    stop_event = asyncio.Event()
    tasks = (
        asyncio.create_task(scheduler_loop(stop_event), name="backup-scheduler"),
        asyncio.create_task(
            customer_display_scheduler_loop(stop_event),
            name="customer-display-scheduler",
        ),
        # 發票佇列自動送出：作廢/折讓先前只會排進佇列、沒有任何東西送出去，
        # 導致帳上作廢而平台上發票仍有效（實測查證）。開立不在此列，見 background_service。
        asyncio.create_task(
            einvoice_scheduler_loop(stop_event),
            name="einvoice-autosend-scheduler",
        ),
        # 線上點餐拉單（docs/44 §5.3）：沒設定雲端就空轉。
        asyncio.create_task(
            online_order_scheduler_loop(stop_event),
            name="online-order-puller",
        ),
    )
    try:
        yield
    finally:
        stop_event.set()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


def create_app() -> FastAPI:
    """建立並設定 FastAPI 應用程式。"""
    app = FastAPI(title="lu-camp API", version="0.1.0", lifespan=_lifespan)
    # CORS：店務認證仍走 Bearer；KIOSK v2 使用 Path-scoped HttpOnly cookie，故明確允許
    # credentials。allow_origins 是列舉值而非 "*"，瀏覽器不會把 cookie 放行給未知來源。
    app.add_middleware(PhotoBodyLimit)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip() for origin in get_settings().cors_origins.split(",") if origin.strip()
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[ERROR_CODE_HEADER],
    )

    @app.middleware("http")
    async def limit_kiosk_body(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # 嚴格政策（Codex 第三輪 medium）：帶 body 的 /kiosk 請求必須有合法 Content-Length
        # ——缺（chunked/串流）一律 411，超上限/非法 413，皆於 JSON 解析「前」擋下。
        # 自家 kiosk 前端必帶 Content-Length，正常流量零影響；schema/服務層為內層防線。
        is_kiosk = request.url.path.startswith(f"{API_PREFIX}/kiosk")
        # 切結書改版是唯一會收「整份長文」的店務端點，同樣在解析前擋大小。
        is_agreement = request.url.path.startswith(f"{API_PREFIX}/agreements")
        if request.method == "POST" and _PHOTO_UPLOAD_PATH.match(request.url.path):
            rejected = _reject_photo_body(request.headers.get("content-length"))
            if rejected is not None:
                return rejected
        if (is_kiosk or is_agreement) and request.method in ("POST", "PUT", "PATCH"):
            limit = KIOSK_MAX_BODY_BYTES if is_kiosk else AGREEMENT_MAX_BODY_BYTES
            content_length = request.headers.get("content-length")
            if content_length is None:
                if is_kiosk:
                    return JSONResponse(
                        status_code=411,
                        content={"detail": "簽署裝置請求必須帶 Content-Length"},
                    )
            else:
                try:
                    too_large = int(content_length) > limit
                except ValueError:
                    too_large = True
                if too_large:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": "請求體過大（簽署裝置上限 1MB）"
                            if is_kiosk
                            else "切結書內容過大"
                        },
                    )
        return await call_next(request)

    @app.get(
        f"{API_PREFIX}/health",
        response_model=HealthResponse,
        operation_id="getHealth",
        tags=["system"],
    )
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    app.include_router(auth_router, prefix=API_PREFIX)
    app.include_router(contacts_router, prefix=API_PREFIX)
    app.include_router(call_ticket_router, prefix=API_PREFIX)
    app.include_router(intake_router, prefix=API_PREFIX)
    app.include_router(cashdrawer_router, prefix=API_PREFIX)
    app.include_router(consignment_router, prefix=API_PREFIX)
    app.include_router(acquisition_router, prefix=API_PREFIX)
    app.include_router(inventory_router, prefix=API_PREFIX)
    app.include_router(bulk_basket_router, prefix=API_PREFIX)
    app.include_router(menu_router, prefix=API_PREFIX)
    app.include_router(online_order_router, prefix=API_PREFIX)
    app.include_router(online_orders_router, prefix=API_PREFIX)
    app.include_router(menu_entries_router, prefix=API_PREFIX)
    app.include_router(purchasing_router, prefix=API_PREFIX)
    app.include_router(stocktake_router, prefix=API_PREFIX)
    app.include_router(settings_router, prefix=API_PREFIX)
    app.include_router(signing_staff_router, prefix=API_PREFIX)
    app.include_router(signing_agreements_router, prefix=API_PREFIX)
    app.include_router(opening_check_router, prefix=API_PREFIX)
    app.include_router(signing_kiosk_router, prefix=API_PREFIX)
    app.include_router(customer_display_staff_router, prefix=API_PREFIX)
    app.include_router(customer_display_kiosk_router, prefix=API_PREFIX)
    app.include_router(sales_router, prefix=API_PREFIX)
    app.include_router(sales_reasons_router, prefix=API_PREFIX)
    app.include_router(returns_router, prefix=API_PREFIX)
    app.include_router(store_router, prefix=API_PREFIX)
    app.include_router(storecredit_router, prefix=API_PREFIX)
    app.include_router(storecredit_store_router, prefix=API_PREFIX)
    app.include_router(reports_router, prefix=API_PREFIX)
    app.include_router(reports_finance_router, prefix=API_PREFIX)
    app.include_router(campaigns_router, prefix=API_PREFIX)
    app.include_router(einvoice_router, prefix=API_PREFIX)
    app.include_router(einvoice_invoices_router, prefix=API_PREFIX)
    app.include_router(backup_router, prefix=API_PREFIX)
    return app


app = create_app()
