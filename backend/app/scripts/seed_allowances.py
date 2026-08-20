"""對已開立發票的銷售建立退貨 → 折讓（G0401），並真的送上 Amego 測試環境。

**必須在 `seed_issue_invoices` 之後跑。** 折讓的前提是原發票已在平台開立
（`record_allowance` 會擋下未開立與手開紙本），所以順序是
`seed_demo` → `seed_issue_invoices` → 本腳本。

流程與真實店面一致，一步都不能少：
建 `RETURN_INVOICE_CONSENT` 簽署任務（客人同意開折讓，作業要點第 9 點）→ 顧客螢幕
確認 → 客人簽名 → 帶著已簽任務建退貨 → 系統開折讓＋排 G0401 → 上送平台。

**只做部分退貨**：累計全退會走「作廢原發票」（F0501）而不是折讓，那是另一條路徑。

執行：

    cd backend
    ALLOW_DEV_SEED=true uv run python -m app.scripts.seed_allowances --count 40
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select, text
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
from app.core.db import get_sessionmaker
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.customerdisplay.models import KioskDevice
from app.modules.einvoice.models import EInvoiceUploadQueue
from app.modules.einvoice.service import EInvoiceService
from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.modules.sales.models import Sale, SaleLine
from app.modules.signing.schemas import SignatureTaskCreate
from app.modules.signing.service import SigningService
from app.scripts.seed_demo import make_signature_png, touch_kiosk
from app.scripts.seed_issue_invoices import (
    IssueFailed,
    _guard_environment,
    _guard_test_seller,
    _make_client,
)
from app.shared.enums import EInvoiceAction, SaleLineType, SignatureTaskKind
from app.shared.exceptions import DomainError

_RETURN_REASONS = (
    "回家後發現尺寸不合",
    "拉鍊有瑕疵",
    "與描述不符",
    "重複購買",
    "家人已有同款",
)


@dataclass
class AllowanceReport:
    created: int = 0
    uploaded: int = 0
    skipped: int = 0
    failed: int = 0

    def render(self, elapsed: float) -> str:
        return "\n".join(
            [
                "# 折讓（G0401）報告",
                "",
                f"- 建立折讓：{self.created:,}",
                f"- 上送成功：{self.uploaded:,}",
                f"- 略過（不符條件）：{self.skipped:,}",
                f"- 失敗：{self.failed:,}",
                f"- 耗時：{elapsed / 60:.1f} 分鐘",
            ]
        )


async def _kiosk_ids(session: AsyncSession, store_id: int) -> tuple[int, int]:
    """取 seed 建好的顧客螢幕與收銀櫃檯。"""
    device = await session.scalar(
        select(KioskDevice).where(KioskDevice.store_id == store_id).order_by(KioskDevice.id)
    )
    if device is None:
        raise IssueFailed("找不到顧客螢幕；請先跑 seed_demo")
    terminal_id = await session.scalar(
        text(
            "SELECT pos_terminal_id FROM terminal_kiosk_pairings"
            " WHERE store_id = :s AND unpaired_at IS NULL LIMIT 1"
        ),
        {"s": store_id},
    )
    if terminal_id is None:
        raise IssueFailed("顧客螢幕尚未與櫃檯配對；請先跑 seed_demo")
    return device.id, int(terminal_id)


async def _candidates(session: AsyncSession, store_id: int, want: int) -> list[int]:
    """挑「發票已在平台開立、尚未退過、且明細可部分退」的銷售。

    **要多行或多件才行**：只有一行一件的單退下去就是累計全退，走的是作廢而不是折讓。
    """
    rows = await session.execute(
        text(
            """
            SELECT s.id
            FROM sales s
            JOIN invoices i ON i.sale_id = s.id
            WHERE s.store_id = :store
              AND s.status = 'COMPLETED'
              AND i.status = 'ISSUED'
              AND i.issue_channel = 'AMEGO'
              AND NOT EXISTS (SELECT 1 FROM returns r WHERE r.sale_id = s.id)
              AND (
                SELECT count(*) FROM sale_lines l
                WHERE l.sale_id = s.id AND l.line_type <> 'MENU'
              ) >= 2
            ORDER BY s.id DESC
            LIMIT :lim
            """
        ),
        {"store": store_id, "lim": want * 6},
    )
    return [int(r[0]) for r in rows.all()]


async def run(*, store_id: int, count: int, rng_seed: int) -> AllowanceReport:
    from app.core.config import get_settings

    _guard_environment(get_settings().database_url)
    rng = random.Random(rng_seed)
    report = AllowanceReport()
    sessionmaker = get_sessionmaker()

    async with sessionmaker() as session:
        await _guard_test_seller(session, store_id)
        device_id, terminal_id = await _kiosk_ids(session, store_id)
        sale_ids = await _candidates(session, store_id, count)
        print(f"候選銷售 {len(sale_ids):,} 筆，目標折讓 {count} 筆", flush=True)

        # **退貨退現金必須在開帳中的班別下進行**（CLAUDE.md §7 不變量 #8）。
        # seed 每天結束都會結帳，所以現在沒有開著的班別——先開一班，跑完再結掉。
        # 這也符合真實情形：退貨本來就發生在營業中、抽屜開著的時候。
        cash = CashDrawerService(session)
        opened_here = False
        if await cash.get_current_session(store_id) is None:
            await cash.open_session(store_id, 1, Decimal(30000))
            await session.commit()
            opened_here = True

        seen_skips: set[str] = set()
        signing = SigningService(session)
        returns_svc = ReturnsService(session)
        for sale_id in sale_ids:
            if report.created >= count:
                break
            sale = await session.get(Sale, sale_id)
            if sale is None:
                continue
            lines = [
                line
                for line in await session.scalars(
                    select(SaleLine).where(
                        SaleLine.sale_id == sale_id, SaleLine.line_type != SaleLineType.MENU
                    )
                )
                if line.net_amount > 0
            ]
            if len(lines) < 2:
                report.skipped += 1
                continue
            target = rng.choice(lines[:-1])  # 留至少一行不退 → 部分退貨
            try:
                await touch_kiosk(session, store_id, device_id)
                task = await signing.create_task(
                    store_id,
                    SignatureTaskCreate(
                        kind=SignatureTaskKind.RETURN_INVOICE_CONSENT,
                        contact_id=sale.buyer_contact_id,
                        content={"lines": [{"sale_line_id": target.id, "qty": 1}]},
                        terminal_id=terminal_id,
                        ref_type="sale",
                        ref_id=sale_id,
                    ),
                    created_by=1,
                )
                await signing.acknowledge_task(store_id, device_id, task.id)
                await signing.sign_task(
                    store_id,
                    task.id,
                    device_id=device_id,
                    signature_image_base64=make_signature_png(rng),
                    chosen_payout=None,
                )
                await returns_svc.create_return(
                    store_id,
                    sale_id=sale_id,
                    lines=[ReturnLineInput(sale_line_id=target.id, qty=1)],
                    reason=rng.choice(_RETURN_REASONS),
                    actor_user_id=1,
                    idempotency_key=f"seed-allow-{sale_id}",
                    consent_signature_task_id=task.id,
                )
            except DomainError as exc:
                await session.rollback()
                report.skipped += 1
                reason = f"{type(exc).__name__}: {exc}"
                if reason not in seen_skips:
                    seen_skips.add(reason)
                    print(f"  跳過原因：{reason}", flush=True)
                continue
            await session.commit()
            report.created += 1

        if opened_here:
            current = await cash.get_current_session(store_id)
            if current is not None:
                await cash.close_session(
                    current, await cash.expected_amount(current), 1
                )
                await session.commit()

        pending = list(
            await session.scalars(
                select(EInvoiceUploadQueue.id).where(
                    EInvoiceUploadQueue.store_id == store_id,
                    EInvoiceUploadQueue.action == EInvoiceAction.ALLOWANCE,
                    EInvoiceUploadQueue.status == "PENDING",
                )
            )
        )
    print(f"已建立折讓 {report.created}，待上送 G0401 {len(pending)} 筆", flush=True)

    async with sessionmaker() as session:
        client = await _make_client(session, store_id)
        svc = EInvoiceService(session)
        for queue_id in pending:
            try:
                await svc.send_via_amego(store_id, queue_id, client=client)
                report.uploaded += 1
            except Exception as exc:  # 平台拒絕/傳輸中斷：記錄後續跑
                print(f"  折讓佇列 {queue_id} 失敗：{type(exc).__name__}", flush=True)
                report.failed += 1
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="建立退貨折讓並上送 Amego（測試環境）")
    parser.add_argument("--store-id", type=int, default=1)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="allowance_report.txt")
    args = parser.parse_args()

    started = time.monotonic()
    report = asyncio.run(
        run(store_id=args.store_id, count=args.count, rng_seed=args.seed)
    )
    rendered = report.render(time.monotonic() - started)
    print(rendered)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(rendered + "\n")
    if report.created == 0:
        print("\n沒有建立任何折讓。", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
