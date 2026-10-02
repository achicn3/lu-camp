"""排隊收購：同一台店員平板上簽切結書（docs/42 §13；店主 2026-10-02）。

客人勾完要賣哪幾件 → 同一台平板直接進簽署頁（切結書、選現金或購物金、簽名）→ 交還店員付款。
可以從簽署頁回上一頁重勾：重勾後再進簽署頁會**作廢舊任務、建新任務**，內容一定是新的。

店內平板簽署的任務不綁顧客螢幕（kiosk_device_id 為空），顧客螢幕讀不到；簽署證據規則與顧客螢幕
相同（簽名圖、內容雜湊、證據雜湊、切結書版本、身分指紋），差別只在事件記「哪位店員的登入」。
"""

from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.main import create_app
from app.modules.signing.models import SignatureTask, SignatureTaskEvent
from app.shared.enums import PayoutMethod, SignatureTaskStatus
from tests.integration.customer_display_helpers import (
    ensure_paired_customer_display,
    signature_png_base64,
)
from tests.integration.test_intake_payment import PATH, Ctx, _ctx, _pay


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _ready_batch(client: httpx.AsyncClient, ctx: Ctx, prices: list[str]) -> dict[str, Any]:
    batch = (
        await client.post(
            PATH,
            json={
                "contact_id": ctx.contact_id,
                "declared_item_count": len(prices),
                "prefill_lines": True,
            },
            headers=ctx.auth,
        )
    ).json()
    for line, price in zip(batch["lines"], prices, strict=True):
        await client.patch(
            f"{PATH}/{batch['id']}/lines/{line['id']}", json={"deal_cost": price}, headers=ctx.auth
        )
    resp = await client.post(f"{PATH}/{batch['id']}/ready", headers=ctx.auth)
    assert resp.status_code == 200, resp.text
    ready: dict[str, Any] = resp.json()
    return ready


async def _confirm(client: httpx.AsyncClient, ctx: Ctx, batch_id: int, kept: list[int]) -> None:
    resp = await client.post(
        f"{PATH}/{batch_id}/customer-confirm", json={"kept_line_ids": kept}, headers=ctx.auth
    )
    assert resp.status_code == 200, resp.text


async def _start(client: httpx.AsyncClient, ctx: Ctx, batch_id: int) -> dict[str, Any]:
    resp = await client.post(f"{PATH}/{batch_id}/tablet-signature", headers=ctx.auth)
    assert resp.status_code == 200, resp.text
    task: dict[str, Any] = resp.json()
    return task


async def _sign(
    client: httpx.AsyncClient,
    ctx: Ctx,
    task_id: int,
    payout: str = "CASH",
    key: str = "k1",
    signature: str | None = None,
) -> httpx.Response:
    return await client.post(
        f"/api/v1/signing/tasks/{task_id}/tablet-sign",
        json={
            "signature_image_base64": signature
            if signature is not None
            else signature_png_base64(),
            "chosen_payout": payout,
            "idempotency_key": key,
        },
        headers=ctx.auth,
    )


def _amounts(task: dict[str, Any]) -> tuple[list[str], str]:
    items = task["content"]["items"]
    return [i["amount"] for i in items], task["content"]["total"]


async def test_confirm_then_sign_on_the_same_tablet_and_pay_with_chosen_payout(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _ready_batch(client, ctx, ["300", "500", "200"])
    await _confirm(client, ctx, batch["id"], [batch["lines"][2]["id"]])
    task = await _start(client, ctx, batch["id"])
    assert task["kind"] == "ACQUISITION_AFFIDAVIT" and task["status"] == "PENDING"
    assert _amounts(task) == (["300", "500"], "800")
    assert task["agreement_body"]  # 切結書全文給客人讀

    row = await db_session.get(SignatureTask, task["id"])
    assert row is not None and row.kiosk_device_id is None  # 不綁顧客螢幕

    signed = await _sign(client, ctx, task["id"], payout="STORE_CREDIT")
    assert signed.status_code == 200, signed.text
    assert (signed.json()["status"], signed.json()["chosen_payout"]) == ("SIGNED", "STORE_CREDIT")
    await db_session.refresh(row)
    assert row.signature_sha256 and row.content_sha256 and row.evidence_hash and row.signed_at
    event = await db_session.scalar(
        select(SignatureTaskEvent)
        .where(SignatureTaskEvent.signature_task_id == row.id)
        .where(SignatureTaskEvent.to_status == SignatureTaskStatus.SIGNED)
    )
    assert event is not None and event.actor_user_id == ctx.clerk_id
    assert event.reason_code == "TABLET_SIGNATURE_ACCEPTED"

    # 付款以客人簽署時選的為準（這裡帶 CASH 也會變成購物金）
    paid = await _pay(client, ctx, batch["id"], payout="CASH")
    assert paid.status_code == 200, paid.text
    await db_session.refresh(row)
    assert (
        row.status is SignatureTaskStatus.CONSUMED
        and row.chosen_payout is PayoutMethod.STORE_CREDIT
    )


async def test_going_back_and_reticking_voids_old_task_and_signs_new_content(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _ready_batch(client, ctx, ["300", "500"])
    await _confirm(client, ctx, batch["id"], [])
    first = await _start(client, ctx, batch["id"])
    assert (await _sign(client, ctx, first["id"])).status_code == 200
    # 客人回上一頁，把 2 號改成不賣，再進簽署頁
    await _confirm(client, ctx, batch["id"], [batch["lines"][1]["id"]])
    second = await _start(client, ctx, batch["id"])
    assert second["id"] != first["id"]
    assert _amounts(second) == (["300"], "300")
    old = await db_session.get(SignatureTask, first["id"])
    assert old is not None
    await db_session.refresh(old)
    assert old.status is SignatureTaskStatus.VOIDED
    # 舊簽名不能拿來付款；新的簽完才付得出去
    assert (await _sign(client, ctx, second["id"], key="k2")).status_code == 200
    paid = await _pay(client, ctx, batch["id"])
    assert paid.status_code == 200, paid.text
    assert paid.json()["accepted_total"] == "300"


async def test_start_again_without_changes_reuses_nothing_stale(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """還在簽署頁（沒簽）就回上一頁再進來：舊的待簽任務作廢、建新的，不會卡「已有進行中任務」。"""
    ctx = await _ctx(db_session, client)
    batch = await _ready_batch(client, ctx, ["300"])
    await _confirm(client, ctx, batch["id"], [])
    first = await _start(client, ctx, batch["id"])
    second = await _start(client, ctx, batch["id"])
    old = await db_session.get(SignatureTask, first["id"])
    assert old is not None
    await db_session.refresh(old)
    assert old.status is SignatureTaskStatus.VOIDED and second["status"] == "PENDING"


async def test_reading_the_current_tablet_task(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """平板重新整理：讀得回目前的簽署任務（內容、切結書）。"""
    ctx = await _ctx(db_session, client)
    batch = await _ready_batch(client, ctx, ["300"])
    await _confirm(client, ctx, batch["id"], [])
    task = await _start(client, ctx, batch["id"])
    again = await client.get(f"{PATH}/{batch['id']}/tablet-signature", headers=ctx.auth)
    assert again.status_code == 200
    assert again.json()["id"] == task["id"] and again.json()["content"]["total"] == "300"


async def test_tablet_sign_replays_same_key(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _ready_batch(client, ctx, ["300"])
    await _confirm(client, ctx, batch["id"], [])
    task = await _start(client, ctx, batch["id"])
    first = await _sign(client, ctx, task["id"], key="same")
    again = await _sign(client, ctx, task["id"], key="same")
    assert (first.status_code, again.status_code) == (200, 200)
    other = await _sign(client, ctx, task["id"], key="different")
    assert other.status_code == 409


async def test_tablet_sign_requires_signature(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _ready_batch(client, ctx, ["300"])
    await _confirm(client, ctx, batch["id"], [])
    task = await _start(client, ctx, batch["id"])
    resp = await client.post(
        f"/api/v1/signing/tasks/{task['id']}/tablet-sign",
        json={"chosen_payout": "CASH", "idempotency_key": "k"},
        headers=ctx.auth,
    )
    assert resp.status_code == 422


async def test_tablet_sign_rejects_split_payout(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _ready_batch(client, ctx, ["300"])
    await _confirm(client, ctx, batch["id"], [])
    task = await _start(client, ctx, batch["id"])
    resp = await _sign(client, ctx, task["id"], payout="SPLIT")
    assert resp.status_code == 422


async def test_tablet_sign_cannot_be_used_on_a_customer_display_task(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """顧客螢幕的任務（綁了裝置）不能由店員帳號代簽。"""
    ctx = await _ctx(db_session, client)
    batch = await _ready_batch(client, ctx, ["300"])
    await _confirm(client, ctx, batch["id"], [])
    terminal, _device = await ensure_paired_customer_display(
        db_session, store_id=ctx.store_id, actor_user_id=ctx.clerk_id
    )
    sent = await client.post(
        f"{PATH}/{batch['id']}/signature", json={"terminal_id": terminal.id}, headers=ctx.auth
    )
    assert sent.status_code == 201, sent.text
    task = {"id": int(sent.json()["signature_task_id"])}
    resp = await _sign(client, ctx, task["id"])
    assert resp.status_code == 404


async def test_tablet_signature_only_after_customer_can_confirm(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = (
        await client.post(
            PATH,
            json={"contact_id": ctx.contact_id, "declared_item_count": 1, "prefill_lines": True},
            headers=ctx.auth,
        )
    ).json()
    resp = await client.post(f"{PATH}/{batch['id']}/tablet-signature", headers=ctx.auth)
    assert resp.status_code == 409
