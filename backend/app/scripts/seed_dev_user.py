"""開發/測試用使用者 seed（**非 migration、勿在正式環境執行**）。

塞入一個開發用使用者（預設 MANAGER），讓前端 /login 與受保護 API 可走通。
密碼**必須由環境變數提供、無預設**（即使是開發帳號也不把密碼寫進 repo）：

    SEED_USER_USERNAME 預設「dev-manager」
    SEED_USER_PASSWORD 必填（未設即報錯退出）
    SEED_USER_ROLE     預設「MANAGER」（可給 CLERK）
    SEED_USER_STORE_ID 預設 1（先跑 seed_dev_store 建門市）

執行：``cd backend && SEED_USER_PASSWORD=<密碼> uv run python -m app.scripts.seed_dev_user``
重跑會以 username 為鍵 upsert（更新密碼/角色/啟用狀態）。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.security import hash_password
from app.modules.user.models import User
from app.shared.enums import UserRole

_ALLOWED_ENVS = frozenset({"development", "test"})


def ensure_dev_environment(app_env: str) -> None:
    """環境防護：非開發/測試環境一律拒跑。

    upsert 以 username 為鍵、會改寫密碼/角色並重新啟用——若誤對正式庫執行，
    等同接管特權帳號（Codex adversarial review 2026-06-11 high）。
    """
    if app_env not in _ALLOWED_ENVS:
        raise SystemExit(
            f"seed_dev_user 僅限開發/測試環境執行（目前 APP_ENV={app_env!r}；"
            f"允許：{sorted(_ALLOWED_ENVS)}）。正式環境帳號管理請走正規流程。"
        )


@dataclass(frozen=True)
class DevUserSeed:
    """一個開發/測試使用者的塞入值。"""

    username: str
    password: str
    role: UserRole
    store_id: int


def seed_from_env(env: Mapping[str, str] | None = None) -> DevUserSeed:
    """由環境變數組出 seed；密碼必填、無預設（不把密碼寫進 repo）。"""
    resolved = os.environ if env is None else env
    password = resolved.get("SEED_USER_PASSWORD", "").strip()
    if not password:
        raise SystemExit("SEED_USER_PASSWORD 未設定；開發帳號密碼也不入 repo，請由環境變數提供。")
    return DevUserSeed(
        username=resolved.get("SEED_USER_USERNAME", "dev-manager"),
        password=password,
        role=UserRole(resolved.get("SEED_USER_ROLE", "MANAGER")),
        store_id=int(resolved.get("SEED_USER_STORE_ID", "1")),
    )


async def upsert_dev_user(session: AsyncSession, seed: DevUserSeed) -> User:
    """以 username 為穩定鍵 upsert：存在則更新密碼/角色/店別並啟用，否則建立。"""
    user = await session.scalar(select(User).where(User.username == seed.username))
    if user is None:
        user = User(
            username=seed.username,
            password_hash=hash_password(seed.password),
            role=seed.role,
            store_id=seed.store_id,
        )
        session.add(user)
    else:
        user.password_hash = hash_password(seed.password)
        user.role = seed.role
        user.store_id = seed.store_id
        user.is_active = True
    await session.flush()
    return user


async def main() -> None:
    """環境防護 → 讀環境變數 → upsert dev user → commit，印出結果摘要（不含密碼）。"""
    ensure_dev_environment(get_settings().app_env)
    seed = seed_from_env()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        user = await upsert_dev_user(session, seed)
        await session.commit()
        print(  # 開發腳本 CLI 輸出（絕不印密碼/雜湊）
            f"seeded dev user id={user.id} username={user.username!r} "
            f"role={user.role} store_id={user.store_id}"
        )


if __name__ == "__main__":
    asyncio.run(main())
