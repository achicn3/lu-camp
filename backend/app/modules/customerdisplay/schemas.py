"""客顯裝置、櫃檯與配對 API schema。"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field


class KioskDeviceLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)
    installation_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    label: str = Field(min_length=1, max_length=100)


class KioskSummary(BaseModel):
    id: int
    label: str


class TerminalSummary(BaseModel):
    id: int
    name: str


class KioskDeviceSessionRead(BaseModel):
    device_id: int
    label: str
    csrf_token: str
    pairing_code: str | None
    pairing_code_expires_at: datetime | None
    paired_terminal: TerminalSummary | None


class KioskDeviceRead(BaseModel):
    device_id: int
    label: str
    pairing_code: str | None
    pairing_code_expires_at: datetime | None
    paired_terminal: TerminalSummary | None


class KioskHeartbeatRequest(BaseModel):
    current_session_id: int | None = Field(default=None, ge=1)
    displayed_revision: Annotated[int, Field(ge=0)]


class KioskHeartbeatRead(BaseModel):
    online: bool
    last_seen_at: datetime


class TerminalCreateRequest(BaseModel):
    installation_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    name: str = Field(min_length=1, max_length=100)


class TerminalRead(BaseModel):
    id: int
    installation_id: str
    name: str
    paired_kiosk: KioskSummary | None


class TerminalPairRequest(BaseModel):
    pairing_code: str = Field(pattern=r"^\d{6}$")


class TerminalUnpairRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=200)
