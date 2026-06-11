"""登入節流（防暴力破解與 argon2 CPU 耗盡；Codex adversarial review 2026-06-11）。

固定視窗失敗計數：per-帳號與 per-IP 兩道門檻，達標即鎖到視窗滑出為止；
節流檢查在密碼驗證**之前**執行，鎖定中的請求不做任何雜湊運算。
登入成功重置該帳號計數（IP 計數保留——同 IP 噴灑多帳號仍受 IP 門檻約束）。

**記憶體有界（第二輪 review high）**：讀路徑（retry_after）不配置任何狀態——
攻擊者以無限唯一 username 試探不會撐爆字典；過期/空桶在觸碰時即刪；寫入時
每字典桶數受 `max_tracked_buckets` 上限約束，超限先清掃過期桶、仍超則逐出
「最後失敗最舊」的桶（O(n) 掃描，成本遠低於一次 argon2，可接受）。

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
    max_tracked_buckets: int = 10_000  # 每字典桶數上限（記憶體有界）


@dataclass
class LoginThrottle:
    """固定視窗登入失敗節流器（時鐘可注入，測試免等待）。"""

    policy: ThrottlePolicy = field(default_factory=ThrottlePolicy)
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self._by_username: dict[str, deque[float]] = {}
        self._by_ip: dict[str, deque[float]] = {}

    def tracked_buckets(self) -> int:
        """目前追蹤的桶總數（觀測/測試用）。"""
        return len(self._by_username) + len(self._by_ip)

    def _prune(self, failures: deque[float], now: float) -> None:
        while failures and now - failures[0] >= self.policy.window_seconds:
            failures.popleft()

    def _locked_for(
        self, store: dict[str, deque[float]], key: str, limit: int, now: float
    ) -> float | None:
        """讀路徑：不為未知 key 配置狀態；過期/空桶觸碰即刪。"""
        failures = store.get(key)
        if failures is None:
            return None
        self._prune(failures, now)
        if not failures:
            del store[key]
            return None
        if len(failures) < limit:
            return None
        return self.policy.window_seconds - (now - failures[0])

    def retry_after(self, username: str, ip: str) -> float | None:
        """鎖定中回剩餘秒數（>0）；可嘗試回 None。讀路徑不配置任何新桶。"""
        now = self.clock()
        locks = [
            lock
            for lock in (
                self._locked_for(
                    self._by_username, username, self.policy.max_failures_per_username, now
                ),
                self._locked_for(self._by_ip, ip, self.policy.max_failures_per_ip, now),
            )
            if lock is not None
        ]
        return max(locks) if locks else None

    def _sweep_expired(self, store: dict[str, deque[float]], now: float) -> None:
        for key in [k for k, failures in store.items() if not failures]:
            del store[key]
        for key in list(store):
            self._prune(store[key], now)
            if not store[key]:
                del store[key]

    def _record(self, store: dict[str, deque[float]], key: str, now: float) -> None:
        failures = store.setdefault(key, deque())
        self._prune(failures, now)
        failures.append(now)
        if len(store) > self.policy.max_tracked_buckets:
            self._sweep_expired(store, now)
        while len(store) > self.policy.max_tracked_buckets:
            # 仍超限：逐出「最後一次失敗最舊」的桶（清掃後各桶必非空）
            oldest = min(store, key=lambda k: store[k][-1])
            del store[oldest]

    def record_failure(self, username: str, ip: str) -> None:
        now = self.clock()
        self._record(self._by_username, username, now)
        self._record(self._by_ip, ip, now)

    def record_success(self, username: str, ip: str) -> None:
        """成功登入：重置（移除）帳號桶；IP 計數保留（防同 IP 噴灑多帳號）。"""
        self._by_username.pop(username, None)
