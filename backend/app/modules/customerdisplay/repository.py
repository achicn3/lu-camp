"""客顯裝置與配對資料存取。"""

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.customerdisplay.models import (
    CartSession,
    KioskDevice,
    KioskDeviceSession,
    KioskPairingCode,
    PosTerminal,
    TerminalKioskPairing,
)
from app.shared.enums import CartSessionStatus


class CustomerDisplayRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, row: object) -> None:
        self._session.add(row)
        await self._session.flush()

    async def get_device_by_installation(
        self,
        kiosk_user_id: int,
        installation_id: str,
        *,
        for_update: bool = False,
    ) -> KioskDevice | None:
        stmt = select(KioskDevice).where(
            KioskDevice.kiosk_user_id == kiosk_user_id,
            KioskDevice.installation_id == installation_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        result: KioskDevice | None = await self._session.scalar(stmt)
        return result

    async def get_device(
        self,
        store_id: int,
        device_id: int,
        *,
        for_update: bool = False,
    ) -> KioskDevice | None:
        stmt = select(KioskDevice).where(
            KioskDevice.store_id == store_id,
            KioskDevice.id == device_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        result: KioskDevice | None = await self._session.scalar(stmt)
        return result

    async def revoke_device_sessions(self, device_id: int, at: datetime) -> None:
        await self._session.execute(
            update(KioskDeviceSession)
            .where(
                KioskDeviceSession.kiosk_device_id == device_id,
                KioskDeviceSession.revoked_at.is_(None),
            )
            .values(revoked_at=at)
        )

    async def get_device_session(
        self,
        token_hash: str,
        *,
        for_update: bool = False,
    ) -> KioskDeviceSession | None:
        stmt = select(KioskDeviceSession).where(
            KioskDeviceSession.token_hash == token_hash,
            KioskDeviceSession.revoked_at.is_(None),
        )
        if for_update:
            stmt = stmt.with_for_update()
        result: KioskDeviceSession | None = await self._session.scalar(stmt)
        return result

    async def get_device_session_by_id(
        self,
        session_id: int,
        *,
        for_update: bool = False,
    ) -> KioskDeviceSession | None:
        stmt = select(KioskDeviceSession).where(
            KioskDeviceSession.id == session_id,
            KioskDeviceSession.revoked_at.is_(None),
        )
        if for_update:
            stmt = stmt.with_for_update()
        result: KioskDeviceSession | None = await self._session.scalar(stmt)
        return result

    async def invalidate_pairing_codes(self, device_id: int, at: datetime) -> None:
        await self._session.execute(
            update(KioskPairingCode)
            .where(
                KioskPairingCode.kiosk_device_id == device_id,
                KioskPairingCode.consumed_at.is_(None),
            )
            .values(consumed_at=at)
        )

    async def get_pairing_code(
        self,
        store_id: int,
        code_hash: str,
        *,
        for_update: bool = False,
    ) -> KioskPairingCode | None:
        stmt = select(KioskPairingCode).where(
            KioskPairingCode.store_id == store_id,
            KioskPairingCode.code_hash == code_hash,
            KioskPairingCode.consumed_at.is_(None),
        )
        if for_update:
            stmt = stmt.with_for_update()
        result: KioskPairingCode | None = await self._session.scalar(stmt)
        return result

    async def get_terminal_by_installation(
        self,
        store_id: int,
        installation_id: str,
        *,
        for_update: bool = False,
    ) -> PosTerminal | None:
        stmt = select(PosTerminal).where(
            PosTerminal.store_id == store_id,
            PosTerminal.installation_id == installation_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        result: PosTerminal | None = await self._session.scalar(stmt)
        return result

    async def get_terminal(
        self,
        store_id: int,
        terminal_id: int,
        *,
        for_update: bool = False,
    ) -> PosTerminal | None:
        stmt = select(PosTerminal).where(
            PosTerminal.store_id == store_id,
            PosTerminal.id == terminal_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        result: PosTerminal | None = await self._session.scalar(stmt)
        return result

    async def get_active_pairing_for_terminal(
        self,
        store_id: int,
        terminal_id: int,
        *,
        for_update: bool = False,
    ) -> TerminalKioskPairing | None:
        stmt = select(TerminalKioskPairing).where(
            TerminalKioskPairing.store_id == store_id,
            TerminalKioskPairing.pos_terminal_id == terminal_id,
            TerminalKioskPairing.unpaired_at.is_(None),
        )
        if for_update:
            stmt = stmt.with_for_update()
        result: TerminalKioskPairing | None = await self._session.scalar(stmt)
        return result

    async def get_active_pairing_for_device(
        self,
        store_id: int,
        device_id: int,
        *,
        for_update: bool = False,
    ) -> TerminalKioskPairing | None:
        stmt = select(TerminalKioskPairing).where(
            TerminalKioskPairing.store_id == store_id,
            TerminalKioskPairing.kiosk_device_id == device_id,
            TerminalKioskPairing.unpaired_at.is_(None),
        )
        if for_update:
            stmt = stmt.with_for_update()
        result: TerminalKioskPairing | None = await self._session.scalar(stmt)
        return result

    async def get_active_cart_for_terminal(
        self,
        store_id: int,
        terminal_id: int,
        *,
        for_update: bool = False,
    ) -> CartSession | None:
        stmt = select(CartSession).where(
            CartSession.store_id == store_id,
            CartSession.pos_terminal_id == terminal_id,
            CartSession.status.in_(
                (
                    CartSessionStatus.DRAFT,
                    CartSessionStatus.FROZEN,
                    CartSessionStatus.PROCESSING,
                    CartSessionStatus.PAYMENT_UNCERTAIN,
                )
            ),
        )
        if for_update:
            stmt = stmt.with_for_update()
        result: CartSession | None = await self._session.scalar(stmt)
        return result

    async def get_active_cart_for_device(
        self,
        store_id: int,
        device_id: int,
    ) -> CartSession | None:
        result: CartSession | None = await self._session.scalar(
            select(CartSession).where(
                CartSession.store_id == store_id,
                CartSession.kiosk_device_id == device_id,
                CartSession.status.in_(
                    (
                        CartSessionStatus.DRAFT,
                        CartSessionStatus.FROZEN,
                        CartSessionStatus.PROCESSING,
                        CartSessionStatus.PAYMENT_UNCERTAIN,
                    )
                ),
            )
        )
        return result

    async def get_active_cart_for_device_by_id(
        self,
        store_id: int,
        device_id: int,
        cart_session_id: int,
    ) -> CartSession | None:
        result: CartSession | None = await self._session.scalar(
            select(CartSession).where(
                CartSession.id == cart_session_id,
                CartSession.store_id == store_id,
                CartSession.kiosk_device_id == device_id,
                CartSession.status.in_(
                    (
                        CartSessionStatus.DRAFT,
                        CartSessionStatus.FROZEN,
                        CartSessionStatus.PROCESSING,
                        CartSessionStatus.PAYMENT_UNCERTAIN,
                    )
                ),
            )
        )
        return result

    async def list_expired_draft_carts(
        self,
        cutoff: datetime,
    ) -> list[CartSession]:
        rows = await self._session.scalars(
            select(CartSession)
            .where(
                CartSession.status == CartSessionStatus.DRAFT,
                CartSession.last_activity_at <= cutoff,
            )
            .with_for_update(skip_locked=True)
        )
        return list(rows)
