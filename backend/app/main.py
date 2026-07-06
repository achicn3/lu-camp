"""FastAPI app factory 與 router 掛載（Phase 0 骨架）。

目前僅提供 `/health` 端點，作為防呆地基的最小可驗證端點，並讓
OpenAPI 合約管線（docs/11）有實際內容可匯出。後續模組依
docs/05-project-structure.md 掛載於此。
"""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import get_settings
from app.modules.acquisition.router import router as acquisition_router
from app.modules.campaigns.router import router as campaigns_router
from app.modules.cashdrawer.router import router as cashdrawer_router
from app.modules.consignment.router import router as consignment_router
from app.modules.contacts.router import router as contacts_router
from app.modules.einvoice.router import invoices_router as einvoice_invoices_router
from app.modules.einvoice.router import router as einvoice_router
from app.modules.inventory.router import router as inventory_router
from app.modules.menu.router import router as menu_router
from app.modules.purchasing.router import router as purchasing_router
from app.modules.reports.finance_router import router as reports_finance_router
from app.modules.reports.router import router as reports_router
from app.modules.returns.router import router as returns_router
from app.modules.sales.router import router as sales_router
from app.modules.settings.router import router as settings_router
from app.modules.signing.router import kiosk_router as signing_kiosk_router
from app.modules.signing.router import staff_router as signing_staff_router
from app.modules.stocktake.router import router as stocktake_router
from app.modules.store.router import router as store_router
from app.modules.storecredit.router import router as storecredit_router
from app.modules.storecredit.router import store_router as storecredit_store_router
from app.modules.user.router import router as auth_router

API_PREFIX = "/api/v1"
# 手持端請求體上限：簽名 base64（≈683KB）＋ JSON 外殼的寬裕值。手持裝置在客人手上，
# 超大 payload 於 JSON 解析「前」即以 Content-Length 擋下（服務層另有解碼前防線）。
KIOSK_MAX_BODY_BYTES = 1_000_000


class HealthResponse(BaseModel):
    """`/health` 回應。"""

    status: str


def create_app() -> FastAPI:
    """建立並設定 FastAPI 應用程式。"""
    app = FastAPI(title="lu-camp API", version="0.1.0")
    # CORS：允許來源由設定提供（CORS_ORIGINS，逗號分隔）。認證走 Bearer 標頭
    # （非 cookie），不需 allow_credentials。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip() for origin in get_settings().cors_origins.split(",") if origin.strip()
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def limit_kiosk_body(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # 嚴格政策（Codex 第三輪 medium）：帶 body 的 /kiosk 請求必須有合法 Content-Length
        # ——缺（chunked/串流）一律 411，超上限/非法 413，皆於 JSON 解析「前」擋下。
        # 自家 kiosk 前端必帶 Content-Length，正常流量零影響；schema/服務層為內層防線。
        if request.url.path.startswith(f"{API_PREFIX}/kiosk") and request.method in (
            "POST",
            "PUT",
            "PATCH",
        ):
            content_length = request.headers.get("content-length")
            if content_length is None:
                return JSONResponse(
                    status_code=411,
                    content={"detail": "簽署裝置請求必須帶 Content-Length"},
                )
            try:
                too_large = int(content_length) > KIOSK_MAX_BODY_BYTES
            except ValueError:
                too_large = True
            if too_large:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "請求體過大（簽署裝置上限 1MB）"},
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
    app.include_router(cashdrawer_router, prefix=API_PREFIX)
    app.include_router(consignment_router, prefix=API_PREFIX)
    app.include_router(acquisition_router, prefix=API_PREFIX)
    app.include_router(inventory_router, prefix=API_PREFIX)
    app.include_router(menu_router, prefix=API_PREFIX)
    app.include_router(purchasing_router, prefix=API_PREFIX)
    app.include_router(stocktake_router, prefix=API_PREFIX)
    app.include_router(settings_router, prefix=API_PREFIX)
    app.include_router(signing_staff_router, prefix=API_PREFIX)
    app.include_router(signing_kiosk_router, prefix=API_PREFIX)
    app.include_router(sales_router, prefix=API_PREFIX)
    app.include_router(returns_router, prefix=API_PREFIX)
    app.include_router(store_router, prefix=API_PREFIX)
    app.include_router(storecredit_router, prefix=API_PREFIX)
    app.include_router(storecredit_store_router, prefix=API_PREFIX)
    app.include_router(reports_router, prefix=API_PREFIX)
    app.include_router(reports_finance_router, prefix=API_PREFIX)
    app.include_router(campaigns_router, prefix=API_PREFIX)
    app.include_router(einvoice_router, prefix=API_PREFIX)
    app.include_router(einvoice_invoices_router, prefix=API_PREFIX)
    return app


app = create_app()
