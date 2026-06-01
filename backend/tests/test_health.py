"""最小整合測試：確認 app 可建立且 /health 回 200（防呆地基的綠燈）。"""

import httpx

from app.main import create_app


async def test_health_ok() -> None:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
