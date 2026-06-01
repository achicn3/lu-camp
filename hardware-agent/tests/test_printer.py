"""硬體代理骨架測試：ESC/POS 指令序列與端點（用 FakePrinter，免實機）。"""

import httpx

from agent.escpos_printer import ESC, GS, FakePrinter, encode_code128, open_drawer, print_label
from agent.main import create_app


def test_open_drawer_kick_sequence() -> None:
    p = FakePrinter()
    open_drawer(p)
    assert bytes(p.buffer) == ESC + b"p" + bytes([0, 25, 250])


def test_print_label_contains_text_and_code128() -> None:
    p = FakePrinter()
    print_label(p, "ABC123", "帳篷", 1500)
    buf = bytes(p.buffer)
    assert "帳篷".encode() in buf
    assert b"NT$1500" in buf
    assert encode_code128("ABC123") in buf
    assert buf.find(GS + b"k") != -1


async def test_label_endpoint_ok() -> None:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/print/label", json={"code": "X1", "name": "n", "price": 10})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_drawer_and_health_endpoints() -> None:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        drawer = await client.post("/drawer/open")
    assert health.json() == {"status": "ok"}
    assert drawer.status_code == 200
