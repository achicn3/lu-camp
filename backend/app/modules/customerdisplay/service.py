"""客顯裝置 session、櫃檯註冊與一次性配對業務邏輯。"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customerdisplay.models import (
    KioskDevice,
    KioskDeviceSession,
    KioskPairingCode,
    PosTerminal,
    TerminalKioskPairing,
)
from app.modules.customerdisplay.repository import CustomerDisplayRepository
from app.modules.user.service import UserService
from app.shared.enums import UserRole

PAIRING_CODE_TTL = timedelta(minutes=5)


class CustomerDisplayError(Exception):
    """客顯領域可預期錯誤。"""


class InvalidKioskCredentials(CustomerDisplayError):
    pass


class InvalidDeviceSession(CustomerDisplayError):
    pass


class InvalidCsrfToken(CustomerDisplayError):
    pass


class PairingConflict(CustomerDisplayError):
    pass


class TerminalNotFound(CustomerDisplayError):
    pass


@dataclass(frozen=True)
class DevicePrincipal:
    session_id: int
    device_id: int
    kiosk_user_id: int
    store_id: int
    csrf_token_hash: str


@dataclass(frozen=True)
class DeviceSessionResult:
    device: KioskDevice
    raw_session_token: str
    raw_csrf_token: str
    pairing_code: str | None
    pairing_code_expires_at: datetime | None
    paired_terminal: PosTerminal | None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _pairing_hash(store_id: int, code: str) -> str:
    return _sha256(f"{store_id}:{code}")


class CustomerDisplayService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = CustomerDisplayRepository(session)

    async def create_device_session(
        self,
        *,
        username: str,
        password: str,
        installation_id: str,
        label: str,
    ) -> DeviceSessionResult:
        """驗 KIOSK 帳密、upsert 實體裝置並輪替其可撤銷 cookie session。"""
        user = await UserService(self._session).authenticate(username, password)
        if user is None or user.role is not UserRole.KIOSK:
            raise InvalidKioskCredentials("帳號或密碼錯誤")
        now = datetime.now(UTC)
        device = await self._repo.get_device_by_installation(
            user.id,
            installation_id.lower(),
            for_update=True,
        )
        if device is None:
            device = KioskDevice(
                store_id=user.store_id,
                kiosk_user_id=user.id,
                installation_id=installation_id.lower(),
                label=label.strip(),
                last_seen_at=now,
            )
            await self._repo.add(device)
        else:
            device.label = label.strip()
            device.is_active = True
            device.last_seen_at = now

        await self._repo.revoke_device_sessions(device.id, now)
        raw_session = secrets.token_urlsafe(32)
        raw_csrf = secrets.token_urlsafe(32)
        device_session = KioskDeviceSession(
            store_id=user.store_id,
            kiosk_device_id=device.id,
            token_hash=_sha256(raw_session),
            csrf_token_hash=_sha256(raw_csrf),
            last_seen_at=now,
        )
        await self._repo.add(device_session)

        pairing = await self._repo.get_active_pairing_for_device(user.store_id, device.id)
        paired_terminal = (
            await self._repo.get_terminal(user.store_id, pairing.pos_terminal_id)
            if pairing is not None
            else None
        )
        code: str | None = None
        expires_at: datetime | None = None
        if pairing is None:
            code, expires_at = await self._replace_pairing_code(device, now)
        return DeviceSessionResult(
            device=device,
            raw_session_token=raw_session,
            raw_csrf_token=raw_csrf,
            pairing_code=code,
            pairing_code_expires_at=expires_at,
            paired_terminal=paired_terminal,
        )

    async def _replace_pairing_code(
        self,
        device: KioskDevice,
        now: datetime,
    ) -> tuple[str, datetime]:
        await self._repo.invalidate_pairing_codes(device.id, now)
        expires_at = now + PAIRING_CODE_TTL
        # 6 位碼只有一百萬種；同店短時間碰撞時重抽，DB partial unique index為最終防線。
        for _ in range(20):
            code = f"{secrets.randbelow(1_000_000):06d}"
            code_hash = _pairing_hash(device.store_id, code)
            existing = await self._repo.get_pairing_code(device.store_id, code_hash)
            if existing is None:
                await self._repo.add(
                    KioskPairingCode(
                        store_id=device.store_id,
                        kiosk_device_id=device.id,
                        code_hash=code_hash,
                        expires_at=expires_at,
                    )
                )
                return code, expires_at
        raise PairingConflict("暫時無法產生配對碼，請稍後再試")

    async def authenticate_device_session(self, raw_token: str | None) -> DevicePrincipal:
        if not raw_token:
            raise InvalidDeviceSession("未提供客顯裝置憑證")
        row = await self._repo.get_device_session(_sha256(raw_token))
        if row is None:
            raise InvalidDeviceSession("客顯裝置憑證無效或已撤銷")
        device = await self._repo.get_device(row.store_id, row.kiosk_device_id)
        if device is None or not device.is_active:
            raise InvalidDeviceSession("客顯裝置已停用")
        user = await UserService(self._session).get_user_in_store(
            row.store_id,
            device.kiosk_user_id,
        )
        if user is None or not user.is_active or user.role is not UserRole.KIOSK:
            raise InvalidDeviceSession("客顯裝置帳號已停用或角色不符")
        return DevicePrincipal(
            session_id=row.id,
            device_id=device.id,
            kiosk_user_id=device.kiosk_user_id,
            store_id=device.store_id,
            csrf_token_hash=row.csrf_token_hash,
        )

    @staticmethod
    def verify_csrf(principal: DevicePrincipal, raw_token: str | None) -> None:
        if not raw_token or not secrets.compare_digest(
            principal.csrf_token_hash,
            _sha256(raw_token),
        ):
            raise InvalidCsrfToken("CSRF token 無效")

    async def device_status(
        self,
        principal: DevicePrincipal,
    ) -> tuple[KioskDevice, PosTerminal | None]:
        device = await self._repo.get_device(principal.store_id, principal.device_id)
        if device is None:
            raise InvalidDeviceSession("客顯裝置不存在")
        pairing = await self._repo.get_active_pairing_for_device(
            principal.store_id,
            principal.device_id,
        )
        terminal = (
            await self._repo.get_terminal(principal.store_id, pairing.pos_terminal_id)
            if pairing is not None
            else None
        )
        return device, terminal

    async def heartbeat(self, principal: DevicePrincipal) -> datetime:
        now = datetime.now(UTC)
        row = await self._repo.get_device_session_by_id(
            principal.session_id,
            for_update=True,
        )
        if row is None:
            raise InvalidDeviceSession("客顯裝置憑證已撤銷")
        device = await self._repo.get_device(
            principal.store_id,
            principal.device_id,
            for_update=True,
        )
        if device is None:
            raise InvalidDeviceSession("客顯裝置不存在")
        row.last_seen_at = now
        device.last_seen_at = now
        return now

    async def register_terminal(
        self,
        store_id: int,
        *,
        installation_id: str,
        name: str,
        actor_user_id: int,
    ) -> PosTerminal:
        terminal = await self._repo.get_terminal_by_installation(
            store_id,
            installation_id.lower(),
            for_update=True,
        )
        now = datetime.now(UTC)
        if terminal is None:
            terminal = PosTerminal(
                store_id=store_id,
                installation_id=installation_id.lower(),
                name=name.strip(),
                created_by=actor_user_id,
                last_seen_at=now,
            )
            await self._repo.add(terminal)
        else:
            terminal.name = name.strip()
            terminal.is_active = True
            terminal.last_seen_at = now
        return terminal

    async def pair_terminal(
        self,
        store_id: int,
        terminal_id: int,
        *,
        pairing_code: str,
        actor_user_id: int,
    ) -> tuple[PosTerminal, KioskDevice]:
        terminal = await self._repo.get_terminal(store_id, terminal_id, for_update=True)
        if terminal is None or not terminal.is_active:
            raise TerminalNotFound("POS 櫃檯不存在")
        code = await self._repo.get_pairing_code(
            store_id,
            _pairing_hash(store_id, pairing_code),
            for_update=True,
        )
        now = datetime.now(UTC)
        if code is None or code.expires_at <= now:
            raise PairingConflict("配對碼無效、已使用或已逾時")
        device = await self._repo.get_device(store_id, code.kiosk_device_id, for_update=True)
        if device is None or not device.is_active:
            raise PairingConflict("配對碼所屬客顯裝置已停用")
        if await self._repo.get_active_pairing_for_terminal(
            store_id,
            terminal.id,
            for_update=True,
        ):
            raise PairingConflict("此 POS 櫃檯已配對客顯，請先解除配對")
        if await self._repo.get_active_pairing_for_device(
            store_id,
            device.id,
            for_update=True,
        ):
            raise PairingConflict("此客顯已配對其他櫃檯，請先解除配對")
        pairing = TerminalKioskPairing(
            store_id=store_id,
            pos_terminal_id=terminal.id,
            kiosk_device_id=device.id,
            paired_by=actor_user_id,
        )
        await self._repo.add(pairing)
        code.consumed_at = now
        await self._session.flush()
        return terminal, device

    async def terminal_read(
        self,
        store_id: int,
        terminal: PosTerminal,
    ) -> tuple[PosTerminal, KioskDevice | None]:
        pairing = await self._repo.get_active_pairing_for_terminal(store_id, terminal.id)
        device = (
            await self._repo.get_device(store_id, pairing.kiosk_device_id)
            if pairing is not None
            else None
        )
        return terminal, device
