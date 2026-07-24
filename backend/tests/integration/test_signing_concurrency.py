"""Signing concurrency invariants for paired devices and expected-state transitions."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, select

import app.core.db as app_db
from app.core.crypto import get_pii_cipher, national_id_blind_index
from app.modules.contacts.models import Contact
from app.modules.customerdisplay.models import (
    KioskDevice,
    PosTerminal,
    TerminalKioskPairing,
)
from app.modules.signing.models import SignatureTask, SignatureTaskEvent
from app.modules.signing.schemas import SignatureTaskCreate
from app.modules.signing.service import SigningService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import PayoutMethod, SignatureTaskKind, SignatureTaskStatus, UserRole
from app.shared.exceptions import (
    SignatureTaskConflict,
    SignatureTaskNotFound,
    SignatureTaskNotPending,
)
from tests.integration.customer_display_helpers import (
    delete_customer_display_rows,
    ensure_paired_customer_display,
    signature_png_base64,
)


async def _seed() -> tuple[int, int, int, int, int]:
    sessionmaker = app_db.get_sessionmaker()
    async with sessionmaker() as session:
        store = Store(name="簽署併發門市")
        session.add(store)
        await session.flush()
        clerk = User(
            store_id=store.id,
            username=f"concurrent-sign-{store.id}",
            password_hash="h",
            role=UserRole.CLERK,
        )
        contact = Contact(
            store_id=store.id,
            name="併發顧客",
            phone="0955555555",
            national_id_enc=get_pii_cipher().encrypt("A123456789"),
            national_id_blind_index=national_id_blind_index("A123456789"),
            roles=["SELLER"],
        )
        session.add_all([clerk, contact])
        await session.flush()
        terminal, device = await ensure_paired_customer_display(
            session,
            store_id=store.id,
            actor_user_id=clerk.id,
        )
        result = (store.id, clerk.id, contact.id, terminal.id, device.id)
        await session.commit()
        return result


def _affidavit(contact_id: int, terminal_id: int, *, amount: str) -> SignatureTaskCreate:
    return SignatureTaskCreate(
        kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
        contact_id=contact_id,
        terminal_id=terminal_id,
        content={"items": [{"name": "品項", "amount": amount}], "total": amount},
    )


async def _cleanup(store_id: int) -> None:
    sessionmaker = app_db.get_sessionmaker()
    async with sessionmaker() as session:
        await delete_customer_display_rows(session, store_id=store_id)
        await session.execute(delete(Contact).where(Contact.store_id == store_id))
        await session.execute(delete(User).where(User.store_id == store_id))
        await session.execute(delete(Store).where(Store.id == store_id))
        await session.commit()


async def test_concurrent_create_keeps_one_active_task_per_device() -> None:
    sessionmaker = app_db.get_sessionmaker()
    store_id, clerk_id, contact_id, terminal_id, _device_id = await _seed()
    try:

        async def create_once(amount: str) -> bool:
            async with sessionmaker() as session:
                try:
                    await SigningService(session).create_task(
                        store_id,
                        _affidavit(contact_id, terminal_id, amount=amount),
                        created_by=clerk_id,
                    )
                    await session.commit()
                    return True
                except SignatureTaskConflict:
                    await session.rollback()
                    return False

        results = await asyncio.gather(create_once("100"), create_once("200"))
        assert sorted(results) == [False, True]
        async with sessionmaker() as session:
            active = list(
                (
                    await session.scalars(
                        select(SignatureTask).where(
                            SignatureTask.store_id == store_id,
                            SignatureTask.status == SignatureTaskStatus.PENDING,
                        )
                    )
                ).all()
            )
            assert len(active) == 1
    finally:
        await _cleanup(store_id)


async def test_two_paired_devices_in_same_store_can_hold_separate_tasks() -> None:
    sessionmaker = app_db.get_sessionmaker()
    store_id, clerk_id, contact_id, terminal_a_id, _device_a_id = await _seed()
    try:
        async with sessionmaker() as session:
            kiosk_user = User(
                store_id=store_id,
                username=f"second-kiosk-{store_id}",
                password_hash="h",
                role=UserRole.KIOSK,
            )
            session.add(kiosk_user)
            await session.flush()
            terminal_b = PosTerminal(
                store_id=store_id,
                installation_id=str(uuid4()),
                name="第二櫃檯",
                created_by=clerk_id,
                last_seen_at=datetime.now(UTC),
            )
            device_b = KioskDevice(
                store_id=store_id,
                kiosk_user_id=kiosk_user.id,
                installation_id=str(uuid4()),
                label="第二客顯",
                last_seen_at=datetime.now(UTC),
            )
            session.add_all([terminal_b, device_b])
            await session.flush()
            session.add(
                TerminalKioskPairing(
                    store_id=store_id,
                    pos_terminal_id=terminal_b.id,
                    kiosk_device_id=device_b.id,
                    paired_by=clerk_id,
                    paired_at=datetime.now(UTC),
                )
            )
            await session.flush()
            signing = SigningService(session)
            first = await signing.create_task(
                store_id,
                _affidavit(contact_id, terminal_a_id, amount="100"),
                created_by=clerk_id,
            )
            second = await signing.create_task(
                store_id,
                _affidavit(contact_id, terminal_b.id, amount="200"),
                created_by=clerk_id,
            )
            await session.commit()
            assert first.kiosk_device_id != second.kiosk_device_id
            assert first.status is second.status is SignatureTaskStatus.PENDING
    finally:
        await _cleanup(store_id)


async def test_sign_and_staff_void_race_has_one_ordered_evidence_chain() -> None:
    sessionmaker = app_db.get_sessionmaker()
    store_id, clerk_id, contact_id, terminal_id, device_id = await _seed()
    try:
        async with sessionmaker() as session:
            signing = SigningService(session)
            task = await signing.create_task(
                store_id,
                _affidavit(contact_id, terminal_id, amount="100"),
                created_by=clerk_id,
            )
            await signing.acknowledge_task(store_id, device_id, task.id)
            task_id = task.id
            await session.commit()

        async def sign() -> str:
            async with sessionmaker() as session:
                try:
                    await SigningService(session).sign_task(
                        store_id,
                        task_id,
                        device_id=device_id,
                        signature_image_base64=signature_png_base64(),
                        chosen_payout=PayoutMethod.CASH,
                    )
                    await session.commit()
                    return "signed"
                except (SignatureTaskNotPending, SignatureTaskNotFound):
                    await session.rollback()
                    return "rejected"

        async def void() -> str:
            async with sessionmaker() as session:
                try:
                    await SigningService(session).cancel_task(
                        store_id,
                        task_id,
                        actor_user_id=clerk_id,
                        reason="併發撤回測試",
                    )
                    await session.commit()
                    return "voided"
                except SignatureTaskNotPending:
                    await session.rollback()
                    return "rejected"

        sign_result, void_result = await asyncio.gather(sign(), void())
        assert void_result == "voided"
        assert sign_result in {"signed", "rejected"}
        async with sessionmaker() as session:
            final_task = await session.get(SignatureTask, task_id)
            assert final_task is not None and final_task.status is SignatureTaskStatus.VOIDED
            events = list(
                (
                    await session.scalars(
                        select(SignatureTaskEvent.to_status)
                        .where(SignatureTaskEvent.signature_task_id == task_id)
                        .order_by(SignatureTaskEvent.id)
                    )
                ).all()
            )
            assert events[:2] == [SignatureTaskStatus.PENDING, SignatureTaskStatus.SIGNING]
            assert events[-1] is SignatureTaskStatus.VOIDED
            if SignatureTaskStatus.SIGNED in events:
                assert events[-2:] == [
                    SignatureTaskStatus.SIGNED,
                    SignatureTaskStatus.VOIDED,
                ]
                assert final_task.signature_image is not None
    finally:
        await _cleanup(store_id)
