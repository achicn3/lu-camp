"""把 seed 產生的待開立發票**真的上送 Amego**（docs/24）。

**與 `seed_demo` 分開是刻意的**：seed 全程離線、十分鐘跑完；上送是對外部平台的真實
動作，要能單獨啟動、單獨中斷、單獨續跑。跑這支就是真的在開發票。

**只能對測試環境跑。** 除了 `seed_demo` 的三道環境守衛之外，另外強制店家統編必須是
Amego 測試統編（`docs/24`：測試公司統編 12345678）——正式統編一旦跑下去就是對國稅局
開出上萬張不存在的交易，不可回復。

冪等：`OrderId` 由 `(store, sale)` 確定性導出，重跑同一批不會重複開立（平台端擋）；
本地已 ISSUED 的直接跳過，所以中斷後再跑就是續跑。

執行：

    cd backend
    ALLOW_DEV_SEED=true uv run python -m app.scripts.seed_issue_invoices --limit 200
    ALLOW_DEV_SEED=true uv run python -m app.scripts.seed_issue_invoices   # 全部
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.modules.backup.models
import app.modules.callticket.models
import app.modules.campaigns.models
import app.modules.cashdrawer.models
import app.modules.consignment.models
import app.modules.customerdisplay.models
import app.modules.einvoice.models
import app.modules.inventory.models
import app.modules.menu.models
import app.modules.purchasing.models
import app.modules.returns.models
import app.modules.sales.models
import app.modules.settings.models
import app.modules.signing.models
import app.modules.stocktake.models
import app.modules.storecredit.models  # noqa: F401
from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.modules.einvoice.amego import AmegoClient, HttpxAmegoTransport
from app.modules.einvoice.models import Invoice
from app.modules.einvoice.service import EInvoiceService
from app.modules.store.models import Store
from app.shared.enums import InvoiceStatus
from app.shared.exceptions import DomainError

# Amego 官方測試公司統編（docs/24）。**不是本店機密**，正式統編絕不寫進 repo。
_AMEGO_TEST_TAX_ID = "12345678"
_ALLOWED_ENVS = {"development", "test"}
_ALLOWED_DB_NAMES = {"lucamp_manual", "lucamp_e2e"}


class IssueFailed(RuntimeError):
    """上送前置條件不成立（環境/統編/設定）。"""


@dataclass
class IssueReport:
    issued: int = 0
    skipped: int = 0
    failed: int = 0
    errors: dict[str, int] = field(default_factory=dict)

    def render(self, elapsed: float) -> str:
        lines = [
            "# Amego 上送報告",
            "",
            f"- 已開立：{self.issued:,}",
            f"- 略過（已開立/不可送）：{self.skipped:,}",
            f"- 失敗：{self.failed:,}",
            f"- 耗時：{elapsed / 60:.1f} 分鐘",
        ]
        if self.errors:
            lines += ["", "## 失敗原因分佈"]
            lines += [
                f"- {reason}：{count:,}"
                for reason, count in sorted(
                    self.errors.items(), key=lambda kv: kv[1], reverse=True
                )
            ]
        return "\n".join(lines)


def _guard_environment(database_url: str) -> None:
    settings = get_settings()
    if settings.app_env not in _ALLOWED_ENVS:
        raise IssueFailed(f"拒絕執行：APP_ENV={settings.app_env}（僅 development/test）")
    if os.environ.get("ALLOW_DEV_SEED") != "true":
        raise IssueFailed("需 ALLOW_DEV_SEED=true 明確 opt-in")
    db_name = database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if db_name not in _ALLOWED_DB_NAMES:
        raise IssueFailed(
            f"拒絕執行：資料庫 `{db_name}` 不在允許清單 {sorted(_ALLOWED_DB_NAMES)}"
        )
    if not settings.amego_app_key.strip():
        raise IssueFailed("AMEGO_APP_KEY 未設定")


async def _guard_test_seller(session: AsyncSession, store_id: int) -> None:
    """**最重要的一道**：賣方統編必須是 Amego 測試統編。

    正式統編跑下去就是對國稅局開出上萬張不存在的交易——不可回復，且是稅務事故。
    """
    store = await session.scalar(select(Store).where(Store.id == store_id))
    if store is None:
        raise IssueFailed(f"找不到 store {store_id}")
    if (store.tax_id or "").strip() != _AMEGO_TEST_TAX_ID:
        raise IssueFailed(
            f"拒絕執行：店家統編為 {store.tax_id!r}，不是 Amego 測試統編 "
            f"{_AMEGO_TEST_TAX_ID}。本腳本只允許對測試環境上送。"
        )


async def _make_client(session: AsyncSession, store_id: int) -> AmegoClient:
    cfg = get_settings()
    store = await session.scalar(select(Store).where(Store.id == store_id))
    assert store is not None  # _guard_test_seller 已驗
    return AmegoClient(
        seller_tax_id=store.tax_id or "",
        app_key=cfg.amego_app_key,
        transport=HttpxAmegoTransport(),
        base_url=cfg.amego_api_base,
    )


async def _worker(
    sale_ids: list[int],
    store_id: int,
    report: IssueReport,
    lock: asyncio.Lock,
    started: float,
    total: int,
) -> None:
    """一條上送工作線：自己的 session，逐筆送出。

    **每筆各自 commit**：上萬筆包在一個交易裡，中途失敗就全部白做，而且平台端
    已經開出去的發票不會跟著回滾——本地與平台就此不一致。
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        for sale_id in sale_ids:
            try:
                await EInvoiceService(session).issue_for_sale(
                    store_id,
                    sale_id,
                    client_factory=lambda: _make_client(session, store_id),
                )
                await session.commit()
                ok, reason = True, ""
            except DomainError as exc:
                await session.rollback()
                ok, reason = False, type(exc).__name__
            except Exception as exc:  # 傳輸中斷等：記錄後續跑，不中止整批
                await session.rollback()
                ok, reason = False, type(exc).__name__
            async with lock:
                if ok:
                    report.issued += 1
                else:
                    report.failed += 1
                    report.errors[reason] = report.errors.get(reason, 0) + 1
                done = report.issued + report.failed
                if done % 100 == 0 or done == total:
                    rate = done / max(time.monotonic() - started, 1e-6)
                    remain = (total - done) / rate if rate > 0 else 0
                    print(
                        f"  {done:,}/{total:,}  成功 {report.issued:,} 失敗 {report.failed:,}"
                        f"  {rate:.1f}/秒  預估剩餘 {remain / 60:.0f} 分",
                        flush=True,
                    )


async def run(*, store_id: int, limit: int | None, concurrency: int) -> IssueReport:
    settings = get_settings()
    _guard_environment(settings.database_url)
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await _guard_test_seller(session, store_id)
        stmt = (
            select(Invoice.sale_id)
            .where(Invoice.store_id == store_id, Invoice.status == InvoiceStatus.PENDING)
            .order_by(Invoice.id)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        sale_ids = list(await session.scalars(stmt))

    report = IssueReport()
    if not sale_ids:
        return report
    print(f"待開立 {len(sale_ids):,} 筆，併發 {concurrency}", flush=True)
    started = time.monotonic()
    lock = asyncio.Lock()
    # 輪流分配（非切段）：各線的工作量與難度分佈接近，避免尾端只剩一條線在跑
    buckets: list[list[int]] = [[] for _ in range(concurrency)]
    for index, sale_id in enumerate(sale_ids):
        buckets[index % concurrency].append(sale_id)
    await asyncio.gather(
        *(
            _worker(bucket, store_id, report, lock, started, len(sale_ids))
            for bucket in buckets
            if bucket
        )
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="把 seed 的待開立發票真的上送 Amego（測試環境）")
    parser.add_argument("--store-id", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None, help="只送前 N 筆（預設全部）")
    parser.add_argument("--concurrency", type=int, default=8, help="同時上送的連線數")
    parser.add_argument("--out", default="issue_report.txt")
    args = parser.parse_args()

    started = time.monotonic()
    report = asyncio.run(
        run(store_id=args.store_id, limit=args.limit, concurrency=args.concurrency)
    )
    rendered = report.render(time.monotonic() - started)
    print(rendered)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(rendered + "\n")
    if report.failed and not report.issued:
        print("\n全數失敗：請檢查憑證與平台狀態。", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
