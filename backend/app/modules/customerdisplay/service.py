"""客顯裝置 session、櫃檯註冊與一次性配對業務邏輯。"""

import hashlib
import json
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.contacts.service import ContactService
from app.modules.customerdisplay.models import (
    CartSession,
    CartSessionEvent,
    KioskDevice,
    KioskDeviceSession,
    KioskPairingCode,
    PosTerminal,
    TerminalKioskPairing,
)
from app.modules.customerdisplay.repository import CustomerDisplayRepository
from app.modules.customerdisplay.schemas import CartTenderRequest, CartUpsertRequest
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.service import SalesService
from app.modules.user.service import UserService
from app.shared.enums import CartSessionStatus, SaleLineType, TenderType, UserRole

PAIRING_CODE_TTL = timedelta(minutes=5)
KIOSK_OFFLINE_AFTER = timedelta(seconds=45)
DRAFT_CART_TTL = timedelta(minutes=30)


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


class CartSessionConflict(CustomerDisplayError):
    pass


class CartSessionInvalid(CustomerDisplayError):
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


def _ntd(value: Decimal) -> str:
    return format(value, "f")


def _mask_member_name(name: str) -> str:
    normalized = unicodedata.normalize("NFC", name.strip())
    if not normalized:
        return "會員"
    if all("\u3400" <= char <= "\u9fff" for char in normalized):
        if len(normalized) == 1:
            return f"{normalized}○"
        if len(normalized) == 2:
            return f"{normalized[0]}○"
        return f"{normalized[0]}{'○' * (len(normalized) - 2)}{normalized[-1]}"
    fields = normalized.split()
    if not fields:
        return "會員"
    return " ".join(f"{field[0]}***" for field in fields if field)


def _line_key(line: SaleLineInput) -> str:
    if line.line_type is SaleLineType.SERIALIZED:
        return f"SERIALIZED:{line.item_code}"
    if line.line_type is SaleLineType.CATALOG:
        return f"CATALOG:{line.catalog_product_id}"
    if line.line_type is SaleLineType.BULK_LOT:
        return f"BULK_LOT:{line.bulk_lot_id}"
    return f"MENU:{line.menu_item_id}"


def _snapshot_fingerprint(snapshot: dict[str, object]) -> str:
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _cart_changes(
    old_snapshot: dict[str, object] | None,
    new_snapshot: dict[str, object],
) -> list[dict[str, object]]:
    def items(snapshot: dict[str, object]) -> list[dict[str, object]]:
        raw = snapshot.get("items")
        if not isinstance(raw, list):
            return []
        return [cast("dict[str, object]", item) for item in raw if isinstance(item, dict)]

    def quantity(item: dict[str, object]) -> int:
        raw = item.get("qty")
        if isinstance(raw, int) and not isinstance(raw, bool):
            return raw
        raise ValueError("購物車快照的商品數量格式錯誤")

    if old_snapshot is None:
        return [
            {
                "type": "ADDED",
                "item_key": str(item["item_key"]),
                "name": str(item["name"]),
                "from_qty": None,
                "to_qty": quantity(item),
            }
            for item in items(new_snapshot)
        ]
    old_items = {str(item["item_key"]): item for item in items(old_snapshot)}
    new_items = {str(item["item_key"]): item for item in items(new_snapshot)}
    changes: list[dict[str, object]] = []
    for key, item in new_items.items():
        old = old_items.get(key)
        if old is None:
            changes.append(
                {
                    "type": "ADDED",
                    "item_key": key,
                    "name": str(item["name"]),
                    "from_qty": None,
                    "to_qty": quantity(item),
                }
            )
        elif quantity(old) != quantity(item):
            changes.append(
                {
                    "type": "QUANTITY_CHANGED",
                    "item_key": key,
                    "name": str(item["name"]),
                    "from_qty": quantity(old),
                    "to_qty": quantity(item),
                }
            )
    for key, item in old_items.items():
        if key not in new_items:
            changes.append(
                {
                    "type": "REMOVED",
                    "item_key": key,
                    "name": str(item["name"]),
                    "from_qty": quantity(item),
                    "to_qty": None,
                }
            )
    return changes


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

    async def issue_pairing_code(
        self,
        principal: DevicePrincipal,
    ) -> tuple[KioskDevice, str, datetime]:
        """未配對裝置重新取得短效一次性代碼；已配對時拒絕，避免誤覆蓋長期關聯。"""
        device = await self._repo.get_device(
            principal.store_id,
            principal.device_id,
            for_update=True,
        )
        if device is None or not device.is_active:
            raise InvalidDeviceSession("客顯裝置不存在或已停用")
        if await self._repo.get_active_pairing_for_device(
            principal.store_id,
            principal.device_id,
            for_update=True,
        ):
            raise PairingConflict("客顯仍與 POS 櫃檯配對，請先由店員解除配對")
        code, expires_at = await self._replace_pairing_code(device, datetime.now(UTC))
        return device, code, expires_at

    async def heartbeat(
        self,
        principal: DevicePrincipal,
        *,
        current_session_id: int | None,
        displayed_revision: int,
    ) -> datetime:
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
        if current_session_id is None:
            if displayed_revision != 0:
                raise CartSessionConflict("待機回報的購物車版本必須為 0")
        else:
            cart = await self._repo.get_active_cart_for_device_by_id(
                principal.store_id,
                principal.device_id,
                current_session_id,
            )
            if cart is None:
                raise CartSessionConflict("客顯回報的購物車已結束或不屬於此裝置")
            if displayed_revision > cart.revision:
                raise CartSessionConflict("客顯回報的版本不存在，請重新載入最新購物車")
        row.last_seen_at = now
        device.last_seen_at = now
        device.displayed_cart_session_id = current_session_id
        device.displayed_revision = displayed_revision
        return now

    @staticmethod
    def kiosk_is_online(device: KioskDevice, *, now: datetime | None = None) -> bool:
        observed_at = now or datetime.now(UTC)
        return (
            device.last_seen_at is not None
            and device.last_seen_at > observed_at - KIOSK_OFFLINE_AFTER
        )

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

    async def get_terminal(
        self,
        store_id: int,
        terminal_id: int,
    ) -> tuple[PosTerminal, KioskDevice | None]:
        terminal = await self._repo.get_terminal(store_id, terminal_id)
        if terminal is None or not terminal.is_active:
            raise TerminalNotFound("POS 櫃檯不存在")
        return await self.terminal_read(store_id, terminal)

    async def unpair_terminal(
        self,
        store_id: int,
        terminal_id: int,
        *,
        reason: str,
        actor_user_id: int,
    ) -> PosTerminal:
        """解除長期配對但保留歷史列與稽核；不刪裝置、不重建櫃檯。"""
        terminal = await self._repo.get_terminal(store_id, terminal_id, for_update=True)
        if terminal is None or not terminal.is_active:
            raise TerminalNotFound("POS 櫃檯不存在")
        pairing = await self._repo.get_active_pairing_for_terminal(
            store_id,
            terminal_id,
            for_update=True,
        )
        if pairing is None:
            raise PairingConflict("此 POS 櫃檯目前沒有配對客顯")
        before_device_id = pairing.kiosk_device_id
        pairing.unpaired_at = datetime.now(UTC)
        await self._session.flush()
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="UNPAIR_CUSTOMER_DISPLAY",
            entity_type="pos_terminal",
            entity_id=str(terminal_id),
            before={"kiosk_device_id": before_device_id},
            after={"kiosk_device_id": None, "reason": reason.strip()},
        )
        return terminal

    @staticmethod
    def _validate_cart_tenders(
        total: Decimal,
        tenders: list[CartTenderRequest] | None,
        *,
        buyer_contact_id: int | None,
    ) -> list[dict[str, str]]:
        if tenders is None:
            return []
        types = [t.tender_type for t in tenders]
        if len(types) != len(set(types)):
            raise CartSessionInvalid("同一付款方式不可重複")
        if sum((t.amount for t in tenders), Decimal(0)) != total:
            raise CartSessionInvalid("付款拆分總和必須等於購物車總額")
        if len(tenders) > 1 and TenderType.STORE_CREDIT not in types:
            raise CartSessionInvalid("混合付款只支援購物金加上一種其他付款方式")
        if TenderType.STORE_CREDIT in types and buyer_contact_id is None:
            raise CartSessionInvalid("使用購物金必須先選擇會員")
        return [
            {"tender_type": tender.tender_type.value, "amount": _ntd(tender.amount)}
            for tender in tenders
        ]

    async def _cart_snapshot(
        self,
        store_id: int,
        data: CartUpsertRequest,
    ) -> dict[str, object]:
        lines = [line.to_input() for line in data.lines]
        quote = await SalesService(self._session).quote_sale(
            store_id,
            lines=lines,
            buyer_contact_id=data.buyer_contact_id,
        )
        items: list[dict[str, object]] = []
        discount_total = Decimal(0)
        for source, quoted in zip(lines, quote.lines, strict=True):
            discount_total += quoted.discount_amount
            items.append(
                {
                    "item_key": _line_key(source),
                    "line_type": quoted.line_type.value,
                    "name": unicodedata.normalize("NFC", quoted.description),
                    "qty": quoted.qty,
                    "unit_price": _ntd(quoted.unit_price),
                    "original_unit_price": (
                        _ntd(quoted.original_unit_price)
                        if quoted.original_unit_price is not None
                        else None
                    ),
                    "discount_amount": _ntd(quoted.discount_amount),
                    "line_total": _ntd(quoted.line_total),
                }
            )
        member: dict[str, str] | None = None
        if data.buyer_contact_id is not None:
            contact = await ContactService(self._session).get_contact(
                store_id,
                data.buyer_contact_id,
            )
            if contact is None:
                raise CartSessionInvalid("會員不存在或不屬本店")
            member = {"display_name": _mask_member_name(contact.name)}
        return {
            "content_version": "cart-v1",
            "items": items,
            "total": _ntd(quote.total),
            "discount_total": _ntd(discount_total),
            "campaign_name": quote.campaign_name,
            "member": member,
            "tenders": self._validate_cart_tenders(
                quote.total,
                data.tenders,
                buyer_contact_id=data.buyer_contact_id,
            ),
        }

    async def upsert_cart(
        self,
        store_id: int,
        terminal_id: int,
        data: CartUpsertRequest,
        *,
        actor_user_id: int,
    ) -> CartSession:
        """建立／更新 DRAFT；revision 不符即 409，同內容回應遺失可冪等回放。"""
        terminal = await self._repo.get_terminal(store_id, terminal_id, for_update=True)
        if terminal is None or not terminal.is_active:
            raise TerminalNotFound("POS 櫃檯不存在")
        pairing = await self._repo.get_active_pairing_for_terminal(
            store_id,
            terminal_id,
            for_update=True,
        )
        if pairing is None:
            raise PairingConflict("POS 櫃檯尚未配對客顯")
        snapshot = await self._cart_snapshot(store_id, data)
        fingerprint = _snapshot_fingerprint(snapshot)
        current = await self._repo.get_active_cart_for_terminal(
            store_id,
            terminal_id,
            for_update=True,
        )
        now = datetime.now(UTC)
        if current is None:
            if data.expected_revision is not None:
                raise CartSessionConflict("購物車版本已結束，請重新讀取後開始下一筆")
            changes = _cart_changes(None, snapshot)
            current = CartSession(
                store_id=store_id,
                pos_terminal_id=terminal_id,
                kiosk_device_id=pairing.kiosk_device_id,
                status=CartSessionStatus.DRAFT,
                revision=1,
                buyer_contact_id=data.buyer_contact_id,
                snapshot=snapshot,
                snapshot_fingerprint=fingerprint,
                last_changes=changes,
                last_activity_at=now,
            )
            await self._repo.add(current)
            await self._repo.add(
                CartSessionEvent(
                    store_id=store_id,
                    cart_session_id=current.id,
                    revision=1,
                    event_type="CART_CREATED",
                    payload={"snapshot": snapshot, "changes": changes},
                    actor_user_id=actor_user_id,
                )
            )
            return current
        if current.status is not CartSessionStatus.DRAFT:
            raise CartSessionConflict("購物車已凍結或正在付款，不可修改")
        if data.expected_revision != current.revision:
            if (
                data.expected_revision == current.revision - 1
                and fingerprint == current.snapshot_fingerprint
            ):
                return current
            raise CartSessionConflict(
                f"購物車版本不符（目前 {current.revision}），請重新讀取後再修改"
            )
        changes = _cart_changes(current.snapshot, snapshot)
        current.revision += 1
        current.buyer_contact_id = data.buyer_contact_id
        current.snapshot = snapshot
        current.snapshot_fingerprint = fingerprint
        current.last_changes = changes
        current.last_activity_at = now
        current.updated_at = now
        await self._session.flush()
        await self._repo.add(
            CartSessionEvent(
                store_id=store_id,
                cart_session_id=current.id,
                revision=current.revision,
                event_type="CART_UPDATED",
                payload={"snapshot": snapshot, "changes": changes},
                actor_user_id=actor_user_id,
            )
        )
        return current

    async def current_cart_for_terminal(
        self,
        store_id: int,
        terminal_id: int,
    ) -> CartSession | None:
        terminal = await self._repo.get_terminal(store_id, terminal_id)
        if terminal is None or not terminal.is_active:
            raise TerminalNotFound("POS 櫃檯不存在")
        return await self._repo.get_active_cart_for_terminal(store_id, terminal_id)

    async def current_cart_for_device(
        self,
        principal: DevicePrincipal,
    ) -> CartSession | None:
        return await self._repo.get_active_cart_for_device(
            principal.store_id,
            principal.device_id,
        )

    async def cancel_cart(
        self,
        store_id: int,
        terminal_id: int,
        *,
        expected_revision: int,
        reason: str,
        actor_user_id: int,
    ) -> CartSession:
        terminal = await self._repo.get_terminal(store_id, terminal_id, for_update=True)
        if terminal is None or not terminal.is_active:
            raise TerminalNotFound("POS 櫃檯不存在")
        current = await self._repo.get_active_cart_for_terminal(
            store_id,
            terminal_id,
            for_update=True,
        )
        if current is None:
            raise CartSessionConflict("目前沒有可清空的購物車")
        if current.status is not CartSessionStatus.DRAFT:
            raise CartSessionConflict("購物車已凍結；請先撤回簽署，再清空購物車")
        if current.revision != expected_revision:
            raise CartSessionConflict(
                f"購物車版本不符（目前 {current.revision}），請重新讀取後再清空"
            )
        current.status = CartSessionStatus.CANCELLED
        current.revision += 1
        current.last_changes = []
        now = datetime.now(UTC)
        current.last_activity_at = now
        current.updated_at = now
        await self._session.flush()
        await self._repo.add(
            CartSessionEvent(
                store_id=store_id,
                cart_session_id=current.id,
                revision=current.revision,
                event_type="CART_CANCELLED",
                payload={"reason": reason.strip()},
                actor_user_id=actor_user_id,
            )
        )
        return current

    async def sweep_expired_carts(self, *, now: datetime | None = None) -> int:
        """將無操作逾 30 分鐘的 DRAFT 原子轉終態，釋放櫃檯／客顯唯一佔用。"""
        observed_at = now or datetime.now(UTC)
        rows = await self._repo.list_expired_draft_carts(observed_at - DRAFT_CART_TTL)
        for cart in rows:
            cart.status = CartSessionStatus.EXPIRED
            cart.revision += 1
            cart.last_changes = []
            cart.updated_at = observed_at
            await self._repo.add(
                CartSessionEvent(
                    store_id=cart.store_id,
                    cart_session_id=cart.id,
                    revision=cart.revision,
                    event_type="CART_EXPIRED",
                    payload={"reason": "DRAFT_IDLE_TTL"},
                    actor_user_id=None,
                )
            )
        return len(rows)
