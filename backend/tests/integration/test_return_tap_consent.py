"""餐點退款的「點選同意」（docs/47 E3，2026-10-01 裁示）。

已開發票的退款要留下買受人同意（電子發票實施作業要點第 9 點）。純餐點退款改成客人在顧客螢幕
點「同意」即可，不必手寫簽名；系統仍留存同意的內容雜湊、時間與裝置，證據鏈不變。

- 只有**本次全部是餐點**的退款可用點選同意；含二手商品一律手寫簽名。
- 點選同意的任務不收簽名圖；手寫簽名的任務不收「點選同意」。
"""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.modules.signing.schemas import SignatureTaskCreate
from app.modules.signing.service import SigningService
from app.shared.enums import SignatureConsentMode, SignatureTaskKind, SignatureTaskStatus
from app.shared.exceptions import InvalidSignatureImage, SignatureTaskConflict
from tests.integration.customer_display_helpers import (
    ensure_paired_customer_display,
    signature_png_base64,
)
from tests.integration.test_returns_invoice_tenders import _issue
from tests.integration.test_returns_menu import _Mixed, _mixed_sale


async def _push(
    session: AsyncSession, m: _Mixed, lines: dict[int, int], mode: SignatureConsentMode
) -> tuple[int, int]:
    terminal, device = await ensure_paired_customer_display(
        session, store_id=m.store_id, actor_user_id=m.clerk_id
    )
    task = await SigningService(session).create_task(
        m.store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.RETURN_INVOICE_CONSENT,
            contact_id=m.member_id,
            content={"lines": [{"sale_line_id": k, "qty": v} for k, v in lines.items()]},
            terminal_id=terminal.id,
            ref_type="sale",
            ref_id=m.sale_id,
            consent_mode=mode,
        ),
        created_by=m.clerk_id,
    )
    await SigningService(session).acknowledge_task(m.store_id, device.id, task.id)
    return task.id, device.id


async def test_food_only_refund_can_be_agreed_by_tap(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    m = await _mixed_sale(db_session)
    await _issue(db_session, m.store_id, m.sale_id, tmp_path)
    task_id, device_id = await _push(db_session, m, {m.latte_line: 1}, SignatureConsentMode.TAP)

    task = await SigningService(db_session).sign_task(
        m.store_id,
        task_id,
        device_id=device_id,
        signature_image_base64=None,
        chosen_payout=None,
        idempotency_key="tap-1",
    )
    assert task.status is SignatureTaskStatus.SIGNED
    assert task.consent_mode is SignatureConsentMode.TAP
    assert task.signature_image is None
    assert task.signature_sha256 is not None and task.evidence_hash is not None

    replay = await SigningService(db_session).sign_task(
        m.store_id,
        task_id,
        device_id=device_id,
        signature_image_base64=None,
        chosen_payout=None,
        idempotency_key="tap-1",
    )
    assert replay.id == task.id  # 回應遺失後同鍵重送：回放，不報錯

    ret = await ReturnsService(db_session).create_return(
        m.store_id,
        sale_id=m.sale_id,
        lines=[ReturnLineInput(m.latte_line, 1)],
        reason="太甜",
        actor_user_id=m.clerk_id,
        idempotency_key="tap-ret",
        consent_signature_task_id=task_id,
    )
    assert ret.refund_amount == 150


async def test_tap_is_refused_when_refund_includes_secondhand(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    m = await _mixed_sale(db_session)
    await _issue(db_session, m.store_id, m.sale_id, tmp_path)
    with pytest.raises(SignatureTaskConflict, match="餐點"):
        await _push(db_session, m, {m.item_line: 1}, SignatureConsentMode.TAP)
    with pytest.raises(SignatureTaskConflict, match="餐點"):
        await _push(db_session, m, {m.latte_line: 1, m.item_line: 1}, SignatureConsentMode.TAP)


async def test_tap_is_only_for_return_consent(db_session: AsyncSession, tmp_path: Path) -> None:
    m = await _mixed_sale(db_session)
    await _issue(db_session, m.store_id, m.sale_id, tmp_path)
    terminal, _ = await ensure_paired_customer_display(
        db_session, store_id=m.store_id, actor_user_id=m.clerk_id
    )
    with pytest.raises(SignatureTaskConflict):
        await SigningService(db_session).create_task(
            m.store_id,
            SignatureTaskCreate(
                kind=SignatureTaskKind.TRANSACTION_ACK,
                contact_id=m.member_id,
                content={},
                terminal_id=terminal.id,
                ref_type="sale",
                ref_id=m.sale_id,
                consent_mode=SignatureConsentMode.TAP,
            ),
            created_by=m.clerk_id,
        )


async def test_tap_task_refuses_a_drawn_signature(db_session: AsyncSession, tmp_path: Path) -> None:
    m = await _mixed_sale(db_session)
    await _issue(db_session, m.store_id, m.sale_id, tmp_path)
    tap_id, device_id = await _push(db_session, m, {m.latte_line: 1}, SignatureConsentMode.TAP)
    with pytest.raises(InvalidSignatureImage):
        await SigningService(db_session).sign_task(
            m.store_id,
            tap_id,
            device_id=device_id,
            signature_image_base64=signature_png_base64(),
            chosen_payout=None,
        )


async def test_signature_task_still_requires_a_drawn_signature(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    m = await _mixed_sale(db_session)
    await _issue(db_session, m.store_id, m.sale_id, tmp_path)
    task_id, device_id = await _push(
        db_session, m, {m.item_line: 1}, SignatureConsentMode.SIGNATURE
    )
    with pytest.raises(InvalidSignatureImage):
        await SigningService(db_session).sign_task(
            m.store_id,
            task_id,
            device_id=device_id,
            signature_image_base64=None,
            chosen_payout=None,
        )
