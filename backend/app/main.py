"""FastAPI app factory 與 router 掛載（Phase 0 骨架）。

目前僅提供 `/health` 端點，作為防呆地基的最小可驗證端點，並讓
OpenAPI 合約管線（docs/11）有實際內容可匯出。後續模組依
docs/05-project-structure.md 掛載於此。
"""

from fastapi import FastAPI
from pydantic import BaseModel

from app.modules.contacts.router import router as contacts_router

API_PREFIX = "/api/v1"


class HealthResponse(BaseModel):
    """`/health` 回應。"""

    status: str


def create_app() -> FastAPI:
    """建立並設定 FastAPI 應用程式。"""
    app = FastAPI(title="lu-camp API", version="0.1.0")

    @app.get(
        f"{API_PREFIX}/health",
        response_model=HealthResponse,
        operation_id="getHealth",
        tags=["system"],
    )
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    app.include_router(contacts_router, prefix=API_PREFIX)
    return app


app = create_app()
