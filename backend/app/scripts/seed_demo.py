"""模擬營運資料（docs/37 P0）：12 個月、含季節性與週間分佈的完整營運樣貌。

**非 migration、勿在正式環境執行。** 取代 `seed_dev_demo.py`（後者仍保留供
`inventory-price-smoke` 使用，見 docs/37 §7.1）。

目的：讓儀表板、報表、分頁、篩選、庫存週轉與現金結算等畫面在截圖時皆為有意義的內容，
同時作為效能觀察樣本。

作法（docs/37 §7.2 混合策略）：
- **黃金路徑走 domain service**：確保狀態機、庫存流轉、現金抽屜、寄售結算等不變條件
  被真正觸發，而不是繞過去直接寫表
- 時間序以「建立後回填 `created_at`」鋪出（報表一律以 `Sale.created_at` 篩選）
- 完成後跑**不變條件驗證**（§7.5）；**驗證失敗即視為 seed 失敗**，不得帶著髒資料
  進入截圖階段

執行：

    cd backend
    ALLOW_DEV_SEED=true uv run python -m app.scripts.seed_demo --seed 42
    ALLOW_DEV_SEED=true uv run python -m app.scripts.seed_demo --purge
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.modules.store.models import Store
from app.modules.user.models import User

_ALLOWED_ENVS = {"development", "test"}
# **資料庫允許清單**（docs/37 §7.9）：硬綁單一名稱會讓它在 e2e 庫上拒跑；
# 但也不能不擋——這支腳本會灌入上萬筆資料，跑錯庫就是災難。
_ALLOWED_DB_NAMES = {"lucamp_manual", "lucamp_e2e"}


class SeedFailed(SystemExit):
    """seed 失敗（含不變條件驗證未過）。用 SystemExit 讓 CLI 直接非零離開。"""


@dataclass
class InvariantResult:
    """一條不變條件的檢查結果。"""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class SeedReport:
    """seed 的產出摘要與驗證結果，供 `seed_verification.txt` 落檔。"""

    counts: dict[str, int] = field(default_factory=dict)
    invariants: list[InvariantResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(i.passed for i in self.invariants)

    def render(self) -> str:
        lines = ["# seed_demo 驗證報告", "", "## 產出數量"]
        lines += [f"- {k}：{v:,}" for k, v in sorted(self.counts.items())]
        lines += ["", "## 不變條件（CLAUDE.md §7）"]
        for inv in self.invariants:
            mark = "✅" if inv.passed else "❌"
            lines.append(f"- {mark} {inv.name}{f'：{inv.detail}' if inv.detail else ''}")
        lines += ["", f"結果：{'全部通過' if self.ok else '**有未通過項目，seed 視為失敗**'}"]
        return "\n".join(lines)


def _guard_environment(database_url: str) -> None:
    """三道環境保護：APP_ENV、明確 opt-in、資料庫名允許清單。

    第三道最重要：前兩道擋不住「APP_ENV 是 development 但 DATABASE_URL 指向
    開發主庫 lucamp」——那正是複審在 docs/37 抓到的 critical 情境。
    """
    settings = get_settings()
    if settings.app_env not in _ALLOWED_ENVS:
        raise SeedFailed(f"拒絕執行：APP_ENV={settings.app_env}（僅 development/test）")
    if os.environ.get("ALLOW_DEV_SEED") != "true":
        raise SeedFailed("需 ALLOW_DEV_SEED=true 明確 opt-in")
    db_name = database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if db_name not in _ALLOWED_DB_NAMES:
        raise SeedFailed(
            f"拒絕執行：資料庫 `{db_name}` 不在允許清單 {sorted(_ALLOWED_DB_NAMES)}。"
            "本腳本會灌入上萬筆資料，跑錯庫就是災難。"
        )


async def _require_prerequisites(session: AsyncSession) -> tuple[int, int]:
    """需要先跑過 seed_dev_store 與 seed_dev_user。回傳 (store_id, manager_id)。"""
    store = await session.scalar(select(Store).where(Store.id == 1))
    manager = await session.scalar(select(User).where(User.username == "dev-manager"))
    if store is None or manager is None:
        raise SeedFailed("請先執行 seed_dev_store 與 seed_dev_user")
    return store.id, manager.id


async def _count(session: AsyncSession, table: str, where: str = "") -> int:
    clause = f" WHERE {where}" if where else ""
    value = await session.scalar(text(f"SELECT count(*) FROM {table}{clause}"))
    return int(value or 0)


async def verify_invariants(session: AsyncSession, store_id: int) -> list[InvariantResult]:
    """CLAUDE.md §7 的領域核心不變量（docs/37 §7.5）。

    **這些檢查在 seed 尚未產生對應資料時會是空集合驗證**——會通過但什麼都沒驗到。
    每條都回報實際檢查到的筆數，讓「通過」與「沒東西可驗」在報告上分得出來，
    不要讓綠燈騙人。
    """
    results: list[InvariantResult] = []

    # 1. 序號商品一旦 SOLD 不可再被售出（同一 serialized_item 不得出現在兩張未作廢的單）
    dup = await session.scalar(
        text(
            "SELECT count(*) FROM ("
            "  SELECT l.serialized_item_id FROM sale_lines l"
            "  JOIN sales s ON s.id = l.sale_id"
            "  WHERE l.store_id = :sid AND l.serialized_item_id IS NOT NULL"
            "    AND s.status <> 'VOIDED'"
            "  GROUP BY l.serialized_item_id HAVING count(*) > 1"
            ") t"
        ),
        {"sid": store_id},
    )
    sold = await _count(
        session,
        "sale_lines l JOIN sales s ON s.id = l.sale_id",
        f"l.store_id = {store_id} AND l.serialized_item_id IS NOT NULL AND s.status <> 'VOIDED'",
    )
    results.append(
        InvariantResult("序號商品不重複售出", int(dup or 0) == 0, f"檢查 {sold} 筆序號銷售行")
    )

    # 11. 每筆銷售的明細金額加總 ＝ 交易總額
    mismatch = await session.scalar(
        text(
            "SELECT count(*) FROM ("
            "  SELECT s.id FROM sales s JOIN sale_lines l ON l.sale_id = s.id"
            "  WHERE s.store_id = :sid AND s.status <> 'VOIDED'"
            "  GROUP BY s.id, s.total HAVING sum(l.net_amount) <> s.total"
            ") t"
        ),
        {"sid": store_id},
    )
    sales_n = await _count(session, "sales", f"store_id = {store_id} AND status <> 'VOIDED'")
    results.append(
        InvariantResult("明細加總＝交易總額", int(mismatch or 0) == 0, f"檢查 {sales_n} 筆銷售")
    )

    # 6. 散裝批 remaining_qty 不得 < 0
    negative = await _count(session, "bulk_lots", f"store_id = {store_id} AND remaining_qty < 0")
    lots = await _count(session, "bulk_lots", f"store_id = {store_id}")
    results.append(
        InvariantResult("散裝批餘量不為負", negative == 0, f"檢查 {lots} 堆")
    )

    # 10. 購物金餘額 ＝ 異動流水加總
    # 餘額在獨立的 `store_credit_accounts.balance`（不在 contacts）；
    # 流水金額欄是 `signed_amount`（帶正負號），不是 `amount`。
    drift = await session.scalar(
        text(
            "SELECT count(*) FROM ("
            "  SELECT a.id FROM store_credit_accounts a"
            "  LEFT JOIN store_credit_ledger g ON g.contact_id = a.contact_id"
            "    AND g.store_id = a.store_id"
            "  WHERE a.store_id = :sid"
            "  GROUP BY a.id, a.balance"
            "  HAVING COALESCE(sum(g.signed_amount), 0) <> a.balance"
            ") t"
        ),
        {"sid": store_id},
    )
    accounts_n = await _count(session, "store_credit_accounts", f"store_id = {store_id}")
    results.append(
        InvariantResult("購物金餘額＝流水加總", int(drift or 0) == 0, f"檢查 {accounts_n} 個帳戶")
    )

    return results


async def purge(session: AsyncSession, store_id: int) -> None:
    """清除本腳本產生的資料（`--purge`）。

    以 `is_seed` 旗標為準（docs/37 §7.8）；尚未實作資料產生時為 no-op。
    """
    _ = store_id
    return None


async def run(*, rng_seed: int, do_purge: bool) -> SeedReport:
    settings = get_settings()
    _guard_environment(settings.database_url)

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        store_id, _manager_id = await _require_prerequisites(session)
        if do_purge:
            await purge(session, store_id)
            await session.commit()
            return SeedReport(counts={"已清除": 1}, invariants=[])

        # TODO(P0 分段建置)：依序加入銷售／收購／寄售／現金班別／簽名任務。
        # 每加一類就在 verify_invariants 補對應檢查，維持「驗證先行」。
        _ = rng_seed

        report = SeedReport()
        report.counts["銷售"] = await _count(session, "sales", f"store_id = {store_id}")
        report.counts["銷售明細"] = await _count(session, "sale_lines", f"store_id = {store_id}")
        report.counts["聯絡人"] = await _count(session, "contacts", f"store_id = {store_id}")
        report.counts["現金班別"] = await _count(
            session, "cash_sessions", f"store_id = {store_id}"
        )
        report.invariants = await verify_invariants(session, store_id)
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description="模擬營運資料（docs/37 P0）")
    parser.add_argument("--seed", type=int, default=42, help="固定亂數種子（相同種子＝相同資料）")
    parser.add_argument("--purge", action="store_true", help="清除本腳本產生的資料")
    parser.add_argument(
        "--out",
        default="seed_verification.txt",
        help="驗證報告輸出路徑",
    )
    args = parser.parse_args()

    report = asyncio.run(run(rng_seed=args.seed, do_purge=args.purge))
    rendered = report.render()
    print(rendered)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(rendered + "\n")
    if not report.ok:
        print("\n不變條件未全數通過：seed 視為失敗。", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
