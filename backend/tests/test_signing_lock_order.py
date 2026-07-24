"""簽署生命週期跨購物車操作的全域鎖順序回歸測試。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from app.modules.signing.service import SigningService
from app.shared.enums import SignatureTaskStatus


class _Session:
    async def flush(self) -> None:
        return None


async def test_kiosk_ack_locks_cart_before_signature_task() -> None:
    """會解凍購物車的任務轉移須與結帳同樣採 cart→task，避免 AB-BA 死結。"""
    calls: list[str] = []
    task = SimpleNamespace(
        id=31,
        store_id=7,
        cart_session_id=19,
        status=SignatureTaskStatus.PENDING,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
        last_user_activity_at=None,
    )

    class _Repo:
        async def get_for_device(
            self,
            store_id: int,
            device_id: int,
            task_id: int,
            *,
            for_update: bool = False,
        ) -> Any:
            calls.append("task_lock" if for_update else "task_preview")
            return task

        async def add_event(self, event: object) -> object:
            return event

    class _DisplayRepo:
        async def get_cart(
            self,
            store_id: int,
            cart_session_id: int,
            *,
            for_update: bool = False,
        ) -> object:
            calls.append("cart_lock" if for_update else "cart_preview")
            return object()

    service = SigningService(_Session())  # type: ignore[arg-type]
    service._repo = _Repo()  # type: ignore[assignment]
    service._display_repo = _DisplayRepo()  # type: ignore[assignment]

    await service.acknowledge_task(7, 11, 31)

    assert calls[:3] == ["task_preview", "cart_lock", "task_lock"]
