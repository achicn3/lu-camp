"""硬體代理 localhost HTTP 服務（Phase 0 骨架）。

提供最小端點：健康檢查、列印標籤、開錢櫃。完整端點（收據/證明聯）依
docs/04 後續補。預設使用 `FakePrinter`，實機接線時注入真實印表機。
"""

from fastapi import FastAPI
from pydantic import BaseModel

from agent.escpos_printer import FakePrinter, SupportsWrite, open_drawer, print_label


class OkResponse(BaseModel):
    status: str


class LabelRequest(BaseModel):
    code: str
    name: str
    price: int


def create_app(printer: SupportsWrite | None = None) -> FastAPI:
    """建立硬體代理應用程式；可注入印表機（預設 FakePrinter）。"""
    device: SupportsWrite = printer if printer is not None else FakePrinter()
    app = FastAPI(title="lu-camp hardware-agent", version="0.1.0")

    @app.get("/health", response_model=OkResponse, operation_id="agentHealth")
    async def health() -> OkResponse:
        return OkResponse(status="ok")

    @app.post("/print/label", response_model=OkResponse, operation_id="printLabel")
    async def label(req: LabelRequest) -> OkResponse:
        print_label(device, req.code, req.name, req.price)
        return OkResponse(status="ok")

    @app.post("/drawer/open", response_model=OkResponse, operation_id="openDrawer")
    async def drawer() -> OkResponse:
        open_drawer(device)
        return OkResponse(status="ok")

    return app


app = create_app()
