"""登入節流（防暴力破解與 argon2 CPU 耗盡；Codex adversarial review 2026-06-11）。

固定視窗失敗計數：per-帳號與 per-IP 兩道門檻，達標即鎖到視窗滑出為止；
節流檢查在密碼驗證**之前**執行，鎖定中的請求不做任何雜湊運算。
登入成功重置該帳號計數（IP 計數保留——同 IP 噴灑多帳號仍受 IP 門檻約束）。

**部署限制（如實聲明）**：單進程記憶體實作，符合本專案「店內單機 uvicorn」部署；
若未來多 worker / 多機，需改共享儲存（Redis 等）——已記入 D-4 auth 強化範圍。
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ThrottlePolicy:
    """節流參數（常數集中，不散落邏輯）。"""

    window_seconds: float = 900.0  # 15 分鐘視窗
    max_failures_per_username: int = 5
    max_failures_per_ip: int = 20


@dataclass
class LoginThrottle:
    """固定視窗登入失敗節流器（時鐘可注入，測試免等待）。"""

    policy: ThrottlePolicy = field(default_factory=ThrottlePolicy)
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self._by_username: dict[str, deque[float]] = {}
        self._by_ip: dict[str, deque[float]] = {}

    def _prune(self, failures: deque[float], now: float) -> None:
        while failures and now - failures[0] >= self.policy.window_seconds:
            failures.popleft()

    def _locked_for(self, failures: deque[float], limit: int, now: float) -> float | None:
        self._prune(failures, now)
        if len(failures) < limit:
            return None
        return self.policy.window_seconds - (now - failures[0])

    def retry_after(self, username: str, ip: str) -> float | None:
        """鎖定中回剩餘秒數（>0）；可嘗試回 None。"""
        now = self.clock()
        username_lock = self._locked_for(
            self._by_username.setdefault(username, deque()),
            self.policy.max_failures_per_username,
            now,
        )
        ip_lock = self._locked_for(
            self._by_ip.setdefault(ip, deque()), self.policy.max_failures_per_ip, now
        )
        locks = [lock for lock in (username_lock, ip_lock) if lock is not None]
        return max(locks) if locks else None

    def record_failure(self, username: str, ip: str) -> None:
        now = self.clock()
        self._by_username.setdefault(username, deque()).append(now)
        self._by_ip.setdefault(ip, deque()).append(now)

    def record_success(self, username: str, ip: str) -> None:
        """成功登入：重置帳號計數；IP 計數保留（防同 IP 噴灑多帳號）。"""
        self._by_username.pop(username, None)
