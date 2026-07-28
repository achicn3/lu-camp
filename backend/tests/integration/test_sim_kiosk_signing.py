"""F-1 回歸：sim_180d 的簽署層必須經真實客顯配對流程產生任務（docs/27 Phase 1、docs/23）。

K 系列客顯重構後，`SigningService.create_task` 要求「已配對且在線的客顯」，
`sign_task` 要求任務先被客顯 ACK，購物金簽署更只能從 POS 權威購物車凍結流程建立。
本測試以真實 service 驗證模擬器的三條簽署鏈仍然成立（收購切結／購物金／交易簽收），
避免模擬器再度靜默退化成「零簽署任務」。
"""

import hashlib
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from qa_e2e.sim_180d import (
    KioskSetup,
    Sim,
    _sign_affidavit,
    _store_credit_sale,
    provision_kiosk,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.canonical import canonical_json_bytes
from app.core.crypto import get_pii_cipher, national_id_blind_index
from app.core.security import hash_password
from app.modules.acquisition.schemas import AcquisitionCreate, AcquisitionItemIn
from app.modules.acquisition.service import AcquisitionService
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.customerdisplay.models import KioskDevice, TerminalKioskPairing
from app.modules.customerdisplay.service import CustomerDisplayService
from app.modules.inventory.models import SerializedItem
from app.modules.sales.models import Sale
from app.modules.signing.models import SignatureTask
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import (
    AcquisitionType,
    Grade,
    PayoutMethod,
    SignatureTaskKind,
    SignatureTaskStatus,
    UserRole,
)

_KIOSK_PASSWORD = "sim-kiosk-test-pw"


class _Fixture:
    def __init__(self, store_id: int, manager_id: int, clerk_id: int, kiosk_username: str) -> None:
        self.store_id = store_id
        self.manager_id = manager_id
        self.clerk_id = clerk_id
        self.kiosk_username = kiosk_username


@pytest_asyncio.fixture
async def env(db_session: AsyncSession) -> AsyncGenerator[_Fixture]:
    store = Store(name="模擬門市")
    db_session.add(store)
    await db_session.flush()
    manager = User(
        store_id=store.id,
        username=f"sim-manager-{store.id}",
        password_hash=hash_password(_KIOSK_PASSWORD),
        role=UserRole.MANAGER,
    )
    clerk = User(
        store_id=store.id,
        username=f"sim-clerk-{store.id}",
        password_hash=hash_password(_KIOSK_PASSWORD),
        role=UserRole.CLERK,
    )
    kiosk = User(
        store_id=store.id,
        username=f"sim-kiosk-{store.id}",
        password_hash=hash_password(_KIOSK_PASSWORD),
        role=UserRole.KIOSK,
    )
    db_session.add_all([manager, clerk, kiosk])
    await db_session.flush()
    yield _Fixture(store.id, manager.id, clerk.id, kiosk.username)


async def _kiosk(db_session: AsyncSession, env: _Fixture) -> KioskSetup:
    return await provision_kiosk(
        db_session,
        env.store_id,
        actor_user_id=env.manager_id,
        username=env.kiosk_username,
        password=_KIOSK_PASSWORD,
    )


def _sim(db_session: AsyncSession, env: _Fixture, kiosk: KioskSetup) -> Sim:
    return Sim(db_session, env.store_id, env.manager_id, env.clerk_id, kiosk=kiosk)


def _assert_simulated_evidence(task: SignatureTask) -> None:
    """簽署證據必須由**模擬時鐘**當場產生：signed_at 落在模擬歷史，且 evidence_hash
    能以 `sign_task` 的同一算式由欄位重算（事後平移 signed_at 會讓雜湊永久失效）。"""
    assert task.signed_at is not None
    assert task.signed_at < datetime.now(UTC) - timedelta(days=1)
    recomputed = hashlib.sha256(
        canonical_json_bytes(
            {
                "task_id": task.id,
                "content_sha256": task.content_sha256,
                "signature_sha256": task.signature_sha256,
                "signed_at": task.signed_at.isoformat(),
            }
        )
    ).hexdigest()
    assert recomputed == task.evidence_hash


async def _seller(db_session: AsyncSession, store_id: int, name: str, phone: str) -> Contact:
    nid = "A123456789"
    contact = Contact(
        store_id=store_id,
        name=name,
        phone=phone,
        national_id_enc=get_pii_cipher().encrypt(nid),
        national_id_blind_index=national_id_blind_index(nid),
        roles=["SELLER", "MEMBER", "CONSIGNOR"],
    )
    db_session.add(contact)
    await db_session.flush()
    return contact


@pytest.mark.asyncio
async def test_provision_kiosk_pairs_an_online_display(
    db_session: AsyncSession, env: _Fixture
) -> None:
    kiosk = await _kiosk(db_session, env)

    device = await db_session.get(KioskDevice, kiosk.device_id)
    assert device is not None
    assert CustomerDisplayService.kiosk_is_online(device)
    pairing = await db_session.scalar(
        select(TerminalKioskPairing).where(
            TerminalKioskPairing.store_id == env.store_id,
            TerminalKioskPairing.pos_terminal_id == kiosk.terminal_id,
            TerminalKioskPairing.kiosk_device_id == kiosk.device_id,
            TerminalKioskPairing.unpaired_at.is_(None),
        )
    )
    assert pairing is not None
    assert kiosk.principal.device_id == kiosk.device_id


@pytest.mark.asyncio
async def test_sim_affidavit_chain_produces_signed_task(
    db_session: AsyncSession, env: _Fixture
) -> None:
    kiosk = await _kiosk(db_session, env)
    sim = _sim(db_session, env, kiosk)
    seller = await _seller(db_session, env.store_id, "簽署測試", "0912000001")

    task_id = await _sign_affidavit(
        sim,
        seller.id,
        [{"name": "帳篷", "amount": "1000"}],
        1000,
        PayoutMethod.CASH,
    )

    task = await db_session.get(SignatureTask, task_id)
    assert task is not None
    assert task.kind is SignatureTaskKind.ACQUISITION_AFFIDAVIT
    assert task.status is SignatureTaskStatus.SIGNED
    assert task.kiosk_device_id == kiosk.device_id
    assert task.chosen_payout is PayoutMethod.CASH
    assert sim.stats["affidavits"] == 1
    assert sim.errors == {}
    _assert_simulated_evidence(task)


@pytest.mark.asyncio
async def test_sim_store_credit_sale_binds_frozen_cart_signature(
    db_session: AsyncSession, env: _Fixture
) -> None:
    kiosk = await _kiosk(db_session, env)
    sim = _sim(db_session, env, kiosk)
    member = await _seller(db_session, env.store_id, "購物金測試", "0912000002")

    consign = await AcquisitionService(db_session).create_acquisition(
        env.store_id,
        env.clerk_id,
        AcquisitionCreate(
            type=AcquisitionType.CONSIGNMENT,
            contact_id=member.id,
            items=[
                AcquisitionItemIn(
                    name="寄售睡袋",
                    grade=Grade.A,
                    listed_price=Decimal(1200),
                    commission_pct=50,
                )
            ],
        ),
        idempotency_key="sim-test-consign-1",
    )
    assert consign.item_codes is not None
    sim.sc_reserved.extend(consign.item_codes)
    await StoreCreditService(db_session).adjust(
        env.store_id,
        member.id,
        amount=Decimal(500),
        reason="測試用購物金",
        created_by=env.manager_id,
        idempotency_key="sim-test-credit-1",
    )
    await CashDrawerService(db_session).open_session(env.store_id, env.clerk_id, Decimal(3000))
    await db_session.commit()

    await _store_credit_sale(sim)

    assert sim.errors == {}
    assert sim.stats["sc_sales"] == 1
    assert sim.stats["scu_tasks"] == 1
    sale = await db_session.scalar(
        select(Sale).where(Sale.store_id == env.store_id).order_by(Sale.id.desc())
    )
    assert sale is not None
    assert sale.signature_task_id is not None
    task = await db_session.get(SignatureTask, sale.signature_task_id)
    assert task is not None
    assert task.kind is SignatureTaskKind.STORE_CREDIT_USE
    assert task.status is SignatureTaskStatus.CONSUMED
    assert task.contact_id == member.id
    assert task.cart_session_id is not None
    assert task.content["store_credit_amount"] == "500"
    assert task.content["total"] == "1200"
    _assert_simulated_evidence(task)
    item = await db_session.scalar(
        select(SerializedItem).where(SerializedItem.item_code == consign.item_codes[0])
    )
    assert item is not None
    assert item.status.value == "SOLD"
