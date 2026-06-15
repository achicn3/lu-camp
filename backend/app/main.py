"""FastAPI app factory 與 router 掛載（Phase 0 骨架）。

目前僅提供 `/health` 端點，作為防呆地基的最小可驗證端點，並讓
OpenAPI 合約管線（docs/11）有實際內容可匯出。後續模組依
docs/05-project-structure.md 掛載於此。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.core.config import get_settings
from app.modules.acquisition.router import router as acquisition_router
from app.modules.cashdrawer.router import router as cashdrawer_router
from app.modules.contacts.router import router as contacts_router
from app.modules.inventory.router import router as inventory_router
from app.modules.reports.router import router as reports_router
from app.modules.sales.router import router as sales_router
from app.modules.settings.router import router as settings_router
from app.modules.store.router import router as store_router
from app.modules.storecredit.router import router as storecredit_router
from app.modules.storecredit.router import store_router as storecredit_store_router
from app.modules.user.router import router as auth_router

API_PREFIX = "/api/v1"


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
    app.include_router(acquisition_router, prefix=API_PREFIX)
    app.include_router(inventory_router, prefix=API_PREFIX)
    app.include_router(settings_router, prefix=API_PREFIX)
    app.include_router(sales_router, prefix=API_PREFIX)
    app.include_router(store_router, prefix=API_PREFIX)
    app.include_router(storecredit_router, prefix=API_PREFIX)
    app.include_router(storecredit_store_router, prefix=API_PREFIX)
    app.include_router(reports_router, prefix=API_PREFIX)
    return app


app = create_app()
