"""sim_180d — 180+ 天門市全真流程模擬（docs/27 Phase 1）。

與 seed_large 的根本差異：**所有業務資料一律經真實 service 流程產生**（收購走
AcquisitionService＋手持切結簽署、寄售結算走付款流程、每日開關帳、退貨/作廢/盤點/
進項發票全走各自 service），不以 raw-insert 繞過業務邏輯。唯一的事後 UPDATE 是
**時間回填**（把當日新增列的時間戳平移到模擬日；業務邏輯真實執行、僅時鐘平移，
沿 seed_large 既有慣例）。

用法（隔離 DB，嚴禁對 dev/pytest 庫執行）：
  DATABASE_URL=postgresql+asyncpg://lucamp:...@127.0.0.1:1234/lucamp_sim \
  APP_ENV=development ALLOW_DEV_SEED=true SIM_DAYS=200 SIM_SEED=20260716 \
  uv run python -m qa_e2e.sim_180d

前置：seed_dev_store、seed_dev_user 已跑（store id=1、dev-manager/dev-clerk/dev-kiosk）。
產出：qa_e2e/sim_manifest.json（筆數/跨度/種子；Phase 2 各層驗證啟動前先核對）。
"""

from __future__ import annotations

import asyncio
import json
import os
import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.modules.acquisition.schemas import (
    AcquisitionCreate,
    AcquisitionItemIn,
    AcquisitionLotIn,
)
from app.modules.acquisition.service import AcquisitionService
from app.modules.campaigns.service import CampaignService
from app.modules.cashdrawer.models import CashMovement, CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.models import ConsignmentSettlement
from app.modules.consignment.service import ConsignmentService
from app.modules.contacts.schemas import ContactCreate
from app.modules.contacts.service import ContactService
from app.modules.menu.service import MenuService
from app.modules.purchasing.schemas import (
    InputInvoiceIn,
    PurchaseOrderCreate,
    PurchaseOrderLineCreate,
    ReceiveLineIn,
    SupplierCreate,
    SupplierUpdate,
)
from app.modules.purchasing.service import PurchasingService
from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.modules.sales.inputs import SaleLineInput, TenderInput
from app.modules.sales.service import SalesService
from app.modules.settings.schemas import SettingsUpdateRequest
from app.modules.settings.service import StoreSettingsService
from app.modules.signing.schemas import SignatureTaskCreate
from app.modules.signing.service import SigningService
from app.modules.stocktake.service import StocktakeService
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import (
    AcquisitionType,
    BulkAcquisitionBasis,
    Grade,
    PayoutMethod,
    SaleLineType,
    SignatureTaskKind,
    TenderType,
)
from qa_e2e.seed_large import BRAND_GEAR, CATALOG_ITEMS, MENU_ITEMS, SUPPLIERS
from qa_e2e.sim_helpers import (
    DayPlan,
    build_schedule,
    make_national_id,
    make_phone,
    signature_png,
    suggested_price,
)

_ALLOWED = {"development", "dev", "local"}
DAYS = int(os.environ.get("SIM_DAYS", "200"))
SEED = int(os.environ.get("SIM_SEED", "20260716"))
_RNG = random.Random(SEED)
_NOW = datetime.now(UTC)

# 時間回填：這些表的白名單時間欄，會被平移到模擬日（僅當日新增列，watermark 以 id 界定）。
_SHIFT_COLS = (
    "created_at",
    "updated_at",
    "signed_at",
    "intake_date",
    "opened_at",
    "closed_at",
    "confirmed_at",
    "received_at",
    "submitted_at",
    "ordered_at",
    "voided_at",
    "changed_at",  # premium_rate_history：政策時間線須與帳本同步平移（Codex 第二輪 P2）
)
_SHIFT_TABLES = (
    "contacts",
    "cash_sessions",
    "cash_movements",
    "acquisitions",
    "serialized_items",
    "bulk_lots",
    "sales",
    "sale_lines",
    "sale_tenders",
    "store_credit_ledger",
    "consignment_settlements",
    "signature_tasks",
    "agreement_versions",
    "purchase_orders",
    "purchase_order_lines",
    "goods_receipts",
    "stock_movements",
    "stocktakes",
    "stocktake_lines",
    "returns",
    "return_lines",
    "audit_log",
    "premium_rate_history",
    "brands",
    "categories",
    "product_models",
    "catalog_products",
    "menu_items",
    "suppliers",
)

FIRST_AFFIDAVIT_DAY = 15  # 之前：未強制簽署（require_acquisition_affidavit 關）
FIRST_SCU_DAY = 30  # 之前：購物金扣抵不需簽署
PREMIUM_BUMP_DAY = 100  # 溢價率 0.10 → 0.12（寫 premium_rate_history）


class Sim:
    """單次模擬執行的共享狀態。"""

    def __init__(self, session: AsyncSession, store_id: int, manager_id: int, clerk_id: int):
        self.s = session
        self.store_id = store_id
        self.manager_id = manager_id
        self.clerk_id = clerk_id
        self.seq = 0
        self.member_ids: list[int] = []
        self.seller_ids: list[int] = []  # 有合法證號者（可簽切結/收購物金）
        self.consignor_ids: list[int] = []
        self.catalog_ids: list[int] = []
        self.menu_ids: list[int] = []
        self.brand_ids: list[int] = []
        self.category_ids: list[int] = []
        self.supplier_ids: list[int] = []
        self.owned_codes: list[str] = []  # 在庫自有序號品 item_code
        self.consign_codes: list[str] = []  # 在庫寄售序號品
        self.sc_reserved: list[str] = []  # 保留給購物金折抵情境的寄售品
        self.bulk_ids: list[int] = []
        self.open_po: list[int] = []  # ORDERED/PARTIAL 可收貨
        self.recent_cash_sales: list[int] = []  # 近日純現金銷售（退貨/簽收候選）
        self.invoice_serial = 0
        self.watermarks: dict[str, int] = {}
        self.stats: dict[str, int] = {
            "sales": 0, "voids": 0, "returns": 0, "buyouts": 0, "consign_intakes": 0,
            "bulk_lots": 0, "affidavits": 0, "scu_tasks": 0, "ack_tasks": 0,
            "pos": 0, "receipts": 0, "input_invoices": 0, "stocktakes": 0,
            "settle_paid": 0, "credit_grants": 0, "sc_sales": 0, "cash_sessions": 0,
            "members": 0,
        }

        self.errors: dict[str, int] = {}

    def key(self, kind: str) -> str:
        self.seq += 1
        return f"sim-{kind}-{self.seq}"

    def note_err(self, where: str, exc: Exception) -> None:
        """例外可見化：每種（位置×型別）首次列印、其後計數，避免靜默吞噬掩蓋流程壞損。"""
        k = f"{where}:{type(exc).__name__}"
        if k not in self.errors:
            print(f"[sim-err] {k}: {str(exc)[:160]}")
        self.errors[k] = self.errors.get(k, 0) + 1

    def next_invoice_number(self) -> str:
        self.invoice_serial += 1
        return f"SM{self.invoice_serial:08d}"


async def _shiftable_columns(session: AsyncSession) -> dict[str, list[str]]:
    rows = (
        await session.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND column_name = ANY(:cols) "
                "AND table_name = ANY(:tabs)"
            ),
            {"cols": list(_SHIFT_COLS), "tabs": list(_SHIFT_TABLES)},
        )
    ).all()
    out: dict[str, list[str]] = {}
    for t, c in rows:
        out.setdefault(t, []).append(c)
    return out


async def _snapshot_watermarks(sim: Sim, cols: dict[str, list[str]]) -> None:
    for tab in cols:
        val = await sim.s.scalar(text(f"SELECT COALESCE(MAX(id), 0) FROM {tab}"))
        sim.watermarks[tab] = int(val or 0)


async def _shift_day(sim: Sim, cols: dict[str, list[str]], day_start: datetime) -> None:
    """把本日新增列（id > watermark）的時間戳平移到模擬日營業時段。

    偏移量以列序決定性計算（同一列所有欄同值：`(id − watermark) × 75s`，上限 11 小時），
    保證 updated_at ≥/= created_at、同表內時間順序＝建立順序，跨表亦大致依當日事件序
    （Codex P1：獨立 random() 會倒置因果）。已知界限（文件化）：對「舊列」的當日更新
    （如舊結算轉 PAID）其 updated_at 不平移——分析口徑一律以事件產生的新列
    （cash_movements/ledger）之時間為準，該些新列有平移。

    store_credit_ledger 等表有 insert-only／守衛觸發器（ADR-012）擋任何 UPDATE；
    時間平移只動時間欄、不碰金額與鏈，故以 session_replication_role=replica 暫時
    跳過觸發器（僅本交易），結束即還原。
    """
    await sim.s.execute(text("SET session_replication_role = replica"))
    for tab, cs in cols.items():
        wm = sim.watermarks[tab]
        sets = ", ".join(
            f"{c} = CAST(:base AS timestamptz)"
            f" + make_interval(secs => LEAST((id - :wm) * 75, 39600))"
            if c != "intake_date"
            else f"{c} = CAST(:day AS date)"
            for c in cs
        )
        await sim.s.execute(
            text(f"UPDATE {tab} SET {sets} WHERE id > :wm"),
            {"base": day_start, "day": day_start.date(), "wm": wm},
        )
    # 一致性修正：簽名時間 ≥ 建立時間；開/關帳定錨在營業時段兩端。
    await sim.s.execute(
        text(
            "UPDATE signature_tasks SET signed_at = created_at + interval '4 minutes' "
            "WHERE id > :wm AND signed_at IS NOT NULL"
        ),
        {"wm": sim.watermarks["signature_tasks"]},
    )
    await sim.s.execute(
        text(
            "UPDATE cash_sessions SET "
            "opened_at = CAST(:base AS timestamptz) - interval '30 minutes', "
            "closed_at = CASE WHEN closed_at IS NOT NULL "
            "THEN CAST(:base AS timestamptz) + interval '11 hours 30 minutes' ELSE NULL END "
            "WHERE id > :wm"
        ),
        {"base": day_start, "wm": sim.watermarks["cash_sessions"]},
    )
    await sim.s.execute(text("SET session_replication_role = DEFAULT"))
    await sim.s.commit()


def _day_start(day_index: int) -> datetime:
    """模擬日 10:00（UTC 表示；日界課題由 Phase 2 報表層檢視）。"""
    return (_NOW - timedelta(days=DAYS - day_index)).replace(
        hour=2, minute=0, second=0, microsecond=0
    )  # UTC 02:00 = 台北 10:00


async def _sign_affidavit(
    sim: Sim,
    contact_id: int,
    items: list[dict[str, str]],
    total: int,
    payout: PayoutMethod,
    lot: dict[str, Any] | None = None,
) -> int:
    """建立並簽署收購切結（K4 全鏈：店員推送 → 手持簽名）。回 task_id。"""
    svc = SigningService(sim.s)
    content: dict[str, Any] = {"items": items, "total": str(total)}
    if lot is not None:
        content["lot"] = lot
    task = await svc.create_task(
        sim.store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT, contact_id=contact_id, content=content
        ),
        created_by=sim.clerk_id,
    )
    await svc.sign_task(
        sim.store_id,
        task.id,
        signature_image_base64=signature_png(_RNG),
        chosen_payout=payout,
        idempotency_key=sim.key("signk"),
    )
    await sim.s.commit()
    sim.stats["affidavits"] += 1
    return task.id


async def _do_buyout(sim: Sim, day: int) -> None:
    seller = _RNG.choice(sim.seller_ids)
    n_items = _RNG.randint(1, 3)
    items: list[AcquisitionItemIn] = []
    aff_items: list[dict[str, str]] = []
    brand_names = sorted(BRAND_GEAR)
    for _ in range(n_items):
        bname = _RNG.choice(brand_names)
        gname, ref_price = _RNG.choice(BRAND_GEAR[bname])
        cost = max(100, int(ref_price * _RNG.uniform(0.35, 0.6)))
        margin = _RNG.choice([40, 45, 45, 50, 55])
        items.append(
            AcquisitionItemIn(
                name=f"{bname} {gname}",
                grade=_RNG.choice([Grade.S, Grade.A, Grade.A, Grade.B, Grade.C]),
                listed_price=suggested_price(cost, margin),
                acquisition_cost=Decimal(cost),
            )
        )
        aff_items.append({"name": f"{bname} {gname}", "amount": str(cost)})
    total = sum(int(i.acquisition_cost or 0) for i in items)
    payout = (
        PayoutMethod.STORE_CREDIT
        if _RNG.random() < 0.25
        else PayoutMethod.CASH
    )
    task_id: int | None = None
    if day >= FIRST_AFFIDAVIT_DAY:
        task_id = await _sign_affidavit(sim, seller, aff_items, total, payout)
    result = await AcquisitionService(sim.s).create_acquisition(
        sim.store_id,
        sim.clerk_id,
        AcquisitionCreate(
            type=AcquisitionType.BUYOUT,
            contact_id=seller,
            items=items,
            payout_method=payout,
            signature_task_id=task_id,
        ),
        idempotency_key=sim.key("acq"),
    )
    await sim.s.commit()
    sim.stats["buyouts"] += 1
    if payout is PayoutMethod.STORE_CREDIT:
        sim.stats["credit_grants"] += 1
    for code in result.item_codes or []:
        sim.owned_codes.append(code)


async def _do_consign_intake(sim: Sim) -> None:
    consignor = _RNG.choice(sim.consignor_ids)
    bname = _RNG.choice(sorted(BRAND_GEAR))
    gname, ref_price = _RNG.choice(BRAND_GEAR[bname])
    result = await AcquisitionService(sim.s).create_acquisition(
        sim.store_id,
        sim.clerk_id,
        AcquisitionCreate(
            type=AcquisitionType.CONSIGNMENT,
            contact_id=consignor,
            items=[
                AcquisitionItemIn(
                    name=f"{bname} {gname}(寄售)",
                    grade=_RNG.choice([Grade.A, Grade.B, Grade.C]),
                    listed_price=Decimal(max(300, int(ref_price * _RNG.uniform(0.7, 1.1)))),
                    commission_pct=_RNG.choice([40, 50, 50, 60]),
                )
            ],
        ),
        idempotency_key=sim.key("acq"),
    )
    await sim.s.commit()
    sim.stats["consign_intakes"] += 1
    for code in result.item_codes or []:
        # 三成保留給購物金折抵情境（活動不折寄售 → 總額＝標價可精準拆帳），其餘進一般銷售池
        if _RNG.random() < 0.3:
            sim.sc_reserved.append(code)
        else:
            sim.consign_codes.append(code)


async def _do_bulk_lot(sim: Sim, day: int) -> None:
    seller = _RNG.choice(sim.seller_ids)
    qty = _RNG.randint(15, 60)
    cost = _RNG.randint(600, 2400)
    basis = _RNG.choice([BulkAcquisitionBasis.BAG, BulkAcquisitionBasis.WEIGHT])
    name = _RNG.choice(["營繩/營釘一批", "雜項餐具一批", "二手書一批", "衣物一批"])
    task_id: int | None = None
    if day >= FIRST_AFFIDAVIT_DAY:
        task_id = await _sign_affidavit(
            sim,
            seller,
            [{"name": name, "amount": str(cost)}],
            cost,
            PayoutMethod.CASH,
            lot={"total_qty": qty, "acquisition_basis": basis.value},
        )
    result = await AcquisitionService(sim.s).create_acquisition(
        sim.store_id,
        sim.clerk_id,
        AcquisitionCreate(
            type=AcquisitionType.BULK_LOT,
            contact_id=seller,
            lot=AcquisitionLotIn(
                name=name,
                acquisition_cost=Decimal(cost),
                acquisition_basis=basis,
                total_qty=qty,
                unit_price=Decimal(_RNG.choice([30, 50, 80, 120])),
            ),
            payout_method=PayoutMethod.CASH,
            signature_task_id=task_id,
        ),
        idempotency_key=sim.key("acq"),
    )
    await sim.s.commit()
    sim.stats["bulk_lots"] += 1
    if result.lot_code is not None:
        from app.modules.inventory.models import BulkLot

        lot_id = await sim.s.scalar(
            select(BulkLot.id).where(
                BulkLot.store_id == sim.store_id, BulkLot.lot_code == result.lot_code
            )
        )
        if lot_id is not None:
            sim.bulk_ids.append(int(lot_id))


async def _one_sale(sim: Sim, day: int) -> None:
    sales_svc = SalesService(sim.s)
    lines: list[SaleLineInput] = []
    for _ in range(_RNG.randint(1, 4)):
        roll = _RNG.random()
        if roll < 0.10 and sim.owned_codes:
            lines.append(
                SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=sim.owned_codes.pop(0))
            )
        elif roll < 0.16 and sim.consign_codes:
            lines.append(
                SaleLineInput(
                    line_type=SaleLineType.SERIALIZED, item_code=sim.consign_codes.pop(0)
                )
            )
        elif roll < 0.28 and sim.bulk_ids:
            qty = _RNG.randint(1, 4)
            # 候選由 SQL 取、選擇由 _RNG 決定：DB random() 不受 SIM_SEED 控制，
            # 會破壞可重現性（Codex 第二輪 P2）。
            lot_ids = (
                await sim.s.execute(
                    text(
                        "SELECT id FROM bulk_lots WHERE store_id = :sid "
                        "AND remaining_qty >= :q AND status = 'ON_SALE' ORDER BY id"
                    ),
                    {"sid": sim.store_id, "q": qty},
                )
            ).scalars().all()
            if not lot_ids:
                continue  # 各批皆售罄（自然 SOLD_OUT 覆蓋），改走其他品類
            lines.append(
                SaleLineInput(
                    line_type=SaleLineType.BULK_LOT,
                    bulk_lot_id=int(_RNG.choice(list(lot_ids))),
                    qty=qty,
                )
            )
        elif roll < 0.52:
            lines.append(
                SaleLineInput(
                    line_type=SaleLineType.MENU,
                    menu_item_id=_RNG.choice(sim.menu_ids),
                    qty=_RNG.randint(1, 3),
                )
            )
        else:
            qty = _RNG.randint(1, 4)
            pids = (
                await sim.s.execute(
                    text(
                        "SELECT id FROM catalog_products WHERE store_id = :sid "
                        "AND quantity_on_hand >= :q ORDER BY id"
                    ),
                    {"sid": sim.store_id, "q": qty},
                )
            ).scalars().all()
            if not pids:
                continue  # 全面缺貨（採購補貨會回補），改走其他品類
            lines.append(
                SaleLineInput(
                    line_type=SaleLineType.CATALOG,
                    catalog_product_id=int(_RNG.choice(list(pids))),
                    qty=qty,
                )
            )
    if not lines:
        return  # 空車不送單（POS 前端本就擋空車）
    buyer = _RNG.choice(sim.member_ids) if _RNG.random() < 0.65 else None
    try:
        sale = await sales_svc.create_sale(
            sim.store_id, sim.clerk_id, lines=lines, buyer_contact_id=buyer,
            idempotency_key=sim.key("sale"),
        )
        await sim.s.commit()
    except Exception as exc:
        sim.note_err("sale", exc)
        await sim.s.rollback()
        return
    sim.stats["sales"] += 1
    sim.recent_cash_sales.append(sale.id)
    if len(sim.recent_cash_sales) > 120:
        sim.recent_cash_sales = sim.recent_cash_sales[-120:]
    # 交易紀錄簽收（K5b）：小樣本，會員單、當場簽收
    if buyer is not None and _RNG.random() < 0.012:
        try:
            svc = SigningService(sim.s)
            task = await svc.create_task(
                sim.store_id,
                SignatureTaskCreate(
                    kind=SignatureTaskKind.TRANSACTION_ACK,
                    contact_id=buyer,
                    content={},
                    ref_type="sale",
                    ref_id=sale.id,
                ),
                created_by=sim.clerk_id,
            )
            await svc.sign_task(
                sim.store_id, task.id,
                signature_image_base64=signature_png(_RNG),
                chosen_payout=None, idempotency_key=sim.key("signk"),
            )
            await sim.s.commit()
            sim.stats["ack_tasks"] += 1
        except Exception as exc:
            sim.note_err("ack", exc)
            await sim.s.rollback()


async def _store_credit_sale(sim: Sim, day: int) -> None:
    """購物金折抵銷售：用寄售品（活動不折寄售 → 總額＝標價可精準拆帳）＋SCU 簽署（生效後）。"""
    if not sim.sc_reserved:
        return
    holders = (
        await sim.s.execute(
            text(
                "SELECT contact_id, balance FROM store_credit_accounts "
                "WHERE store_id = :sid AND balance > 0 ORDER BY contact_id"
            ),
            {"sid": sim.store_id},
        )
    ).all()
    if not holders:
        return
    row = _RNG.choice(holders)
    cid, bal = int(row[0]), Decimal(row[1])
    from app.modules.inventory.models import SerializedItem

    code = sim.sc_reserved.pop(0)
    item = await sim.s.scalar(select(SerializedItem).where(SerializedItem.item_code == code))
    if item is None:
        return
    total = int(item.listed_price)
    use = min(int(bal), max(1, total - 1))
    task_id: int | None = None
    try:
        if day >= FIRST_SCU_DAY:
            svc = SigningService(sim.s)
            task = await svc.create_task(
                sim.store_id,
                SignatureTaskCreate(
                    kind=SignatureTaskKind.STORE_CREDIT_USE,
                    contact_id=cid,
                    content={"debit": str(use), "sale_total": str(total)},
                ),
                created_by=sim.clerk_id,
            )
            await svc.sign_task(
                sim.store_id, task.id,
                signature_image_base64=signature_png(_RNG),
                chosen_payout=None, idempotency_key=sim.key("signk"),
            )
            await sim.s.commit()
            task_id = task.id
            sim.stats["scu_tasks"] += 1
        await SalesService(sim.s).create_sale(
            sim.store_id, sim.clerk_id,
            lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
            buyer_contact_id=cid,
            tenders=[
                TenderInput(tender_type=TenderType.STORE_CREDIT, amount=Decimal(use)),
                TenderInput(tender_type=TenderType.CASH, amount=Decimal(total - use)),
            ],
            idempotency_key=sim.key("sale"),
            signature_task_id=task_id,
        )
        await sim.s.commit()
        sim.stats["sc_sales"] += 1
        sim.stats["sales"] += 1
    except Exception as exc:
        sim.note_err("sc_sale", exc)
        await sim.s.rollback()


async def _maybe_void_and_return(sim: Sim) -> None:
    sales_svc = SalesService(sim.s)
    # 作廢 ~2%：挑近日單
    if sim.recent_cash_sales and _RNG.random() < 0.45:
        sid = _RNG.choice(sim.recent_cash_sales)
        sale = await sales_svc.get_sale(sim.store_id, sid)
        if sale is not None:
            try:
                await sales_svc.void_sale(sale, sim.clerk_id)
                await sim.s.commit()
                sim.stats["voids"] += 1
                sim.recent_cash_sales.remove(sid)
            except Exception as exc:
                sim.note_err("void", exc)
                await sim.s.rollback()
    # 退貨 ~1%（API 層流程；4B UI 已擱置）
    if sim.recent_cash_sales and _RNG.random() < 0.22:
        sid = _RNG.choice(sim.recent_cash_sales)
        lines = await sales_svc.get_lines(sid)
        # 含序號品（自有＋寄售）：寄售退貨會反轉結算（未付→CANCELLED），高風險路徑
        # 必須有代表性資料（Codex P2）。序號品退貨數量固定 1。
        returnable = [
            ln
            for ln in lines
            if ln.line_type
            in (SaleLineType.CATALOG, SaleLineType.BULK_LOT, SaleLineType.SERIALIZED)
        ]
        if returnable:
            ln = _RNG.choice(returnable)
            try:
                await ReturnsService(sim.s).create_return(
                    sim.store_id,
                    sale_id=sid,
                    lines=[ReturnLineInput(sale_line_id=ln.id, qty=1)],
                    reason=_RNG.choice(["尺寸不合", "客人反悔", "商品瑕疵"]),
                    actor_user_id=sim.clerk_id,
                    idempotency_key=sim.key("ret"),
                )
                await sim.s.commit()
                sim.stats["returns"] += 1
                sim.recent_cash_sales.remove(sid)
            except Exception as exc:
                sim.note_err("return", exc)
                await sim.s.rollback()


async def _po_step(sim: Sim, day_date: date) -> None:
    purch = PurchasingService(sim.s)
    if sim.open_po and (_RNG.random() < 0.55 or len(sim.open_po) > 6):
        po_id = sim.open_po.pop(0)
        po = await purch.get_purchase_order(sim.store_id, po_id)
        if po is None:
            return
        pending = [
            (ln.id, ln.qty - ln.received_qty) for ln in po.lines if ln.qty > ln.received_qty
        ]
        if not pending:
            return
        partial = _RNG.random() < 0.3 and len(pending) > 1
        recv_lines = [
            ReceiveLineIn(line_id=lid, qty=(max(1, q // 2) if partial else q))
            for lid, q in pending
        ]
        invoice: InputInvoiceIn | None = None
        total = 0
        for rl in recv_lines:
            ln = next(x for x in po.lines if x.id == rl.line_id)
            total += rl.qty * int(ln.unit_cost)
        if _RNG.random() < 0.7 and total > 0:
            invoice = InputInvoiceIn(
                invoice_number=sim.next_invoice_number(),
                invoice_date=day_date,
                invoice_total=Decimal(total),
            )
        try:
            _, receipt = await purch.receive_purchase_order(
                sim.store_id, po_id,
                actor_user_id=sim.clerk_id,
                lines=recv_lines,
                idempotency_key=sim.key("recv"),
                request_fingerprint=sim.key("rfp"),
                invoice=invoice,
            )
            await sim.s.commit()
            sim.stats["receipts"] += 1
            if invoice is not None:
                sim.stats["input_invoices"] += 1
            elif total > 0 and _RNG.random() < 0.5:
                # 漏登 → 事後補登一次
                await purch.register_input_invoice(
                    sim.store_id, po_id, receipt.id,
                    invoice=InputInvoiceIn(
                        invoice_number=sim.next_invoice_number(),
                        invoice_date=day_date,
                        invoice_total=Decimal(total),
                    ),
                )
                await sim.s.commit()
                sim.stats["input_invoices"] += 1
            if partial:
                sim.open_po.append(po_id)
        except Exception as exc:
            sim.note_err("po_receive", exc)
            await sim.s.rollback()
    else:
        n_lines = _RNG.randint(1, 4)
        chosen = _RNG.sample(sim.catalog_ids, min(n_lines, len(sim.catalog_ids)))
        try:
            po = await purch.create_purchase_order(
                sim.store_id,
                PurchaseOrderCreate(
                    supplier_id=_RNG.choice(sim.supplier_ids),
                    lines=[
                        PurchaseOrderLineCreate(
                            catalog_product_id=cid,
                            qty=_RNG.randint(30, 120),
                            unit_cost=Decimal(_RNG.choice([40, 60, 90, 120, 200])),
                        )
                        for cid in chosen
                    ],
                    submit=True,
                ),
                actor_user_id=sim.manager_id,
            )
            await sim.s.commit()
            sim.stats["pos"] += 1
            if _RNG.random() < 0.05:
                await purch.cancel_purchase_order(
                    sim.store_id, po.id, actor_user_id=sim.manager_id
                )
                await sim.s.commit()
            else:
                sim.open_po.append(po.id)
        except Exception as exc:
            sim.note_err("po_create", exc)
            await sim.s.rollback()


async def _settlement_payouts(sim: Sim) -> None:
    ids = (
        await sim.s.execute(
            select(ConsignmentSettlement.id)
            .where(
                ConsignmentSettlement.store_id == sim.store_id,
                ConsignmentSettlement.status == "PENDING",
            )
            .order_by(ConsignmentSettlement.id)
            .limit(25)
        )
    ).scalars().all()
    svc = ConsignmentService(sim.s)
    for sid in ids:
        if _RNG.random() < 0.8:  # 留少量 PENDING 供帳齡/待撥報表
            try:
                await svc.pay_settlement(
                    sim.store_id, sid, actor_user_id=sim.clerk_id,
                    idempotency_key=sim.key("payout"),
                )
                await sim.s.commit()
                sim.stats["settle_paid"] += 1
            except Exception as exc:
                sim.note_err("settle", exc)
                await sim.s.rollback()


async def _stocktake(sim: Sim) -> None:
    svc = StocktakeService(sim.s)
    try:
        st = await svc.create_stocktake(sim.store_id, actor_user_id=sim.manager_id)
        await sim.s.commit()
        got = await svc.get_stocktake(sim.store_id, st.id)
        assert got is not None
        counts: dict[int, int] = {}
        for line in got.lines:
            delta = _RNG.choice([0, 0, 0, 0, 0, 0, 0, -1, -2, 1])
            counts[line.catalog_product_id] = max(0, line.system_qty + delta)
        await svc.confirm_stocktake(
            sim.store_id, st.id, counts, actor_user_id=sim.manager_id
        )
        await sim.s.commit()
        sim.stats["stocktakes"] += 1
    except Exception as exc:
        sim.note_err("stocktake", exc)
        await sim.s.rollback()


async def _new_members(sim: Sim, n: int) -> None:
    contacts = ContactService(sim.s)
    for _ in range(n):
        i = sim.stats["members"]
        with_nid = _RNG.random() < 0.45
        roles = ["MEMBER"]
        if with_nid:
            roles.append("SELLER")
        try:
            c = await contacts.create_contact(
                sim.store_id,
                ContactCreate(
                    name=f"{_RNG.choice('陳林黃張李王吳劉蔡楊')}{_RNG.choice('冠宇家豪雅婷怡君志明淑芬建宏美玲俊傑')}",
                    phone=make_phone(_RNG, 10_000_000 + i),
                    national_id=make_national_id(_RNG) if with_nid else None,
                    roles=roles,
                ),
            )
            await sim.s.commit()
        except Exception as exc:
            sim.note_err("member", exc)
            await sim.s.rollback()
            continue
        sim.stats["members"] += 1
        sim.member_ids.append(c.id)
        if with_nid:
            sim.seller_ids.append(c.id)


async def _bootstrap(sim: Sim) -> None:
    """Day 0 前置：品牌/分類/型號、數量型商品、菜單、供應商、寄售人、初始會員。"""
    from app.modules.inventory.models import Brand, Category, ProductModel

    settings_svc = StoreSettingsService(sim.s)
    await settings_svc.update_settings(
        sim.store_id,
        actor_user_id=sim.manager_id,
        patch=SettingsUpdateRequest(monthly_fixed_cash_outflow=Decimal(150000)),
    )
    await sim.s.commit()

    brand_ids: dict[str, int] = {}
    for bname in BRAND_GEAR:
        b = Brand(store_id=sim.store_id, name=bname)
        sim.s.add(b)
        await sim.s.flush()
        brand_ids[bname] = b.id
    sim.brand_ids = list(brand_ids.values())
    for cname, margin in [
        ("帳篷", 40), ("睡眠系統", 42), ("桌椅", 45), ("照明", 48),
        ("炊事", 45), ("消耗品", 35), ("收納", 50), ("服飾", 50),
    ]:
        c = Category(store_id=sim.store_id, name=cname, target_margin_pct=margin)
        sim.s.add(c)
        await sim.s.flush()
        sim.category_ids.append(c.id)
    for bname, gears in BRAND_GEAR.items():
        for gname, _ in gears:
            sim.s.add(
                ProductModel(store_id=sim.store_id, brand_id=brand_ids[bname], name=gname)
            )
    await sim.s.commit()

    from app.modules.inventory.models import CatalogProduct

    for sku, name, price in CATALOG_ITEMS:
        p = CatalogProduct(
            store_id=sim.store_id, sku=sku, name=name, unit_price=Decimal(price),
            quantity_on_hand=_RNG.randint(300, 900), reorder_point=_RNG.choice([20, 30, 50]),
        )
        sim.s.add(p)
        await sim.s.flush()
        sim.catalog_ids.append(p.id)
    await sim.s.commit()

    menu_svc = MenuService(sim.s)
    for i, (name, price, cat) in enumerate(MENU_ITEMS):
        mi = await menu_svc.create_menu_item(
            sim.store_id, name=name, unit_price=Decimal(price), category=cat,
            sort_order=i, actor_user_id=sim.manager_id,
        )
        sim.menu_ids.append(mi.id)
    await sim.s.commit()

    purch = PurchasingService(sim.s)
    for sname in SUPPLIERS:
        s = await purch.create_supplier(sim.store_id, SupplierCreate(name=sname))
        sim.supplier_ids.append(s.id)
    await sim.s.commit()

    contacts = ContactService(sim.s)
    for i in range(60):
        consignor = await contacts.create_contact(
            sim.store_id,
            ContactCreate(
                name=f"寄售人{i + 1:02d}",
                phone=make_phone(_RNG, 20_000_000 + i),
                national_id=make_national_id(_RNG),
                roles=["CONSIGNOR", "MEMBER"],
            ),
        )
        sim.consignor_ids.append(consignor.id)
        sim.member_ids.append(consignor.id)
    await sim.s.commit()
    await _new_members(sim, 250)


async def _mid_sim_adjustments(sim: Sim, day: int) -> None:
    settings_svc = StoreSettingsService(sim.s)
    if day == FIRST_AFFIDAVIT_DAY:
        await settings_svc.update_settings(
            sim.store_id, actor_user_id=sim.manager_id,
            patch=SettingsUpdateRequest(require_acquisition_affidavit=True),
        )
        await sim.s.commit()
    if day == FIRST_SCU_DAY:
        await settings_svc.update_settings(
            sim.store_id, actor_user_id=sim.manager_id,
            patch=SettingsUpdateRequest(require_store_credit_signing=True),
        )
        await sim.s.commit()
    if day == PREMIUM_BUMP_DAY:
        await settings_svc.update_settings(
            sim.store_id, actor_user_id=sim.manager_id,
            patch=SettingsUpdateRequest(premium_rate=Decimal("0.12")),
        )
        await sim.s.commit()
    if day == 60:
        # 人工購物金調整（限 MANAGER、寫 audit）：客訴補償情境，覆蓋 ADJUSTMENT 分錄
        holder = await sim.s.scalar(
            text(
                "SELECT contact_id FROM store_credit_accounts "
                "WHERE store_id = :sid AND balance > 0 ORDER BY id LIMIT 1"
            ),
            {"sid": sim.store_id},
        )
        if holder is not None:
            await StoreCreditService(sim.s).adjust(
                sim.store_id,
                int(holder),
                amount=Decimal(100),
                reason="模擬客訴補償（人工校正）",
                created_by=sim.manager_id,
                idempotency_key=sim.key("scadj"),
            )
            await sim.s.commit()
    if day == 120 and len(sim.supplier_ids) >= 2:
        # 供應商改名（驗歷史單名稱快照不被改寫）＋停用（驗不進建單選單、建單被擋）
        purch = PurchasingService(sim.s)
        renamed = await purch.get_supplier(sim.store_id, sim.supplier_ids[0])
        if renamed is not None:
            await purch.update_supplier(
                sim.store_id, sim.supplier_ids[0],
                SupplierUpdate(name=f"{renamed.name}（改組後新名）"),
                actor_user_id=sim.manager_id,
            )
        await purch.set_supplier_active(
            sim.store_id, sim.supplier_ids[1], False, actor_user_id=sim.manager_id
        )
        await sim.s.commit()
        sim.supplier_ids.pop(1)  # 停用者不再用於建單


async def _campaigns(sim: Sim, day: int) -> None:
    """3 檔歷史活動（起訖對齊模擬日）＋近 21 天 1 檔進行中。"""
    camp = CampaignService(sim.s)
    plans = [
        ("開幕回饋 85 折", 15, 170, 158, True),
        ("仲夏露營季 9 折", 10, 110, 96, True),
        ("週年慶 8 折", 20, 60, 50, True),
        ("秋季感謝祭 9 折", 10, 21, -10, False),  # 進行中：起於 21 天前、迄於未來
    ]
    for name, pct, s_ago, e_ago, ended in plans:
        if day != DAYS - s_ago:
            continue
        # 折扣引擎以真實 now 判斷生效（get_effective）：活動「在模擬期間 ACTIVE 的日子」
        # 其視窗必須涵蓋真實 now，歷史銷售才真的吃到折扣（Codex P2：過去視窗＝白開活動）。
        # 先以涵蓋 now 的視窗建立/啟用，結束日 end() 後再把視窗回填成模擬歷史區間。
        cp = await camp.create_campaign(
            sim.store_id, name=name, discount_pct=pct,
            starts_at=_NOW - timedelta(hours=1), ends_at=_NOW + timedelta(days=400),
            applies_owned_serialized=True, applies_owned_bulk=True,
            applies_catalog=True, applies_consignment=False,
            created_by=sim.manager_id,
        )
        await camp.activate(sim.store_id, cp.id, actor_user_id=sim.manager_id)
        await sim.s.commit()
        sim.s.info.setdefault("live_campaigns", {})[cp.id] = (
            DAYS - e_ago if ended else None,
            s_ago,
            e_ago,
        )


async def _end_due_campaigns(sim: Sim, day: int) -> None:
    camp = CampaignService(sim.s)
    live: dict[int, tuple[int | None, int, int]] = sim.s.info.get("live_campaigns", {})
    for cid, (end_day, s_ago, e_ago) in list(live.items()):
        if end_day is not None and day >= end_day:
            try:
                await camp.end(sim.store_id, cid, actor_user_id=sim.manager_id)
                # 視窗回填為模擬歷史區間（僅時間欄；折扣留痕/金額不受影響）
                await sim.s.execute(
                    text(
                        "UPDATE campaigns SET starts_at = :s, ends_at = :e WHERE id = :cid"
                    ),
                    {
                        "s": _NOW - timedelta(days=s_ago),
                        "e": _NOW - timedelta(days=e_ago),
                        "cid": cid,
                    },
                )
                await sim.s.commit()
            except Exception as exc:
                sim.note_err("camp_end", exc)
                await sim.s.rollback()
            live.pop(cid, None)


async def _expected_cash(sim: Sim, session_id: int, opening: Decimal) -> Decimal:
    total = await sim.s.scalar(
        select(func.coalesce(func.sum(CashMovement.amount), 0)).where(
            CashMovement.session_id == session_id
        )
    )
    return opening + Decimal(total or 0)


async def _run_day(sim: Sim, plan: DayPlan, cols: dict[str, list[str]]) -> None:
    day = plan.day_index
    day_start = _day_start(day)
    await _snapshot_watermarks(sim, cols)
    await _mid_sim_adjustments(sim, day)
    await _campaigns(sim, day)
    await _end_due_campaigns(sim, day)

    cash = CashDrawerService(sim.s)
    opening = Decimal(_RNG.choice([3000, 5000, 5000, 8000]))
    cash_session = await cash.open_session(sim.store_id, sim.clerk_id, opening)
    await sim.s.commit()
    session_id = cash_session.id
    sim.stats["cash_sessions"] += 1

    await _new_members(sim, _RNG.randint(6, 14))
    if plan.po_action:
        await _po_step(sim, day_start.date())
    if plan.stocktake_day:
        await _stocktake(sim)
    for _ in range(plan.n_buyout):
        try:
            await _do_buyout(sim, day)
        except Exception as exc:
            sim.note_err("buyout", exc)
            await sim.s.rollback()
    for _ in range(plan.n_consign_intake):
        try:
            await _do_consign_intake(sim)
        except Exception as exc:
            sim.note_err("consign", exc)
            await sim.s.rollback()
    if plan.make_bulk_lot:
        try:
            await _do_bulk_lot(sim, day)
        except Exception as exc:
            sim.note_err("bulk", exc)
            await sim.s.rollback()
    for _ in range(plan.n_sales):
        await _one_sale(sim, day)
    if day >= 20 and _RNG.random() < 0.8:
        await _store_credit_sale(sim, day)
    await _maybe_void_and_return(sim)
    if plan.settle_payout_day:
        await _settlement_payouts(sim)

    # 關帳：實點＝期望，~5% 天故意差 ±10..100 元（記差異；期望公式不變量 Phase 2 逐一驗）
    expected = await _expected_cash(sim, session_id, opening)
    counted = expected
    if _RNG.random() < 0.05:
        counted = expected + Decimal(_RNG.choice([-100, -50, -10, 10, 50, 100]))
    locked = await sim.s.get(CashSession, session_id)
    assert locked is not None
    await cash.close_session(locked, counted, sim.clerk_id)
    await sim.s.commit()

    await _shift_day(sim, cols, day_start)


async def _write_manifest(sim: Sim) -> None:
    tables = [
        "contacts", "sales", "sale_lines", "acquisitions", "serialized_items", "bulk_lots",
        "signature_tasks", "consignment_settlements", "store_credit_ledger", "cash_sessions",
        "cash_movements", "purchase_orders", "goods_receipts", "stocktakes", "returns",
        "audit_log", "campaigns", "menu_items",
    ]
    counts: dict[str, int] = {}
    for t in tables:
        counts[t] = int(await sim.s.scalar(text(f"SELECT COUNT(*) FROM {t}")) or 0)
    span = (
        await sim.s.execute(text("SELECT MIN(created_at), MAX(created_at) FROM sales"))
    ).one()
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "seed": SEED,
        "days": DAYS,
        "date_span": [str(span[0]), str(span[1])],
        "counts": counts,
        "stats": sim.stats,
        "errors": sim.errors,
    }
    Path(__file__).with_name("sim_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


async def _main() -> None:
    settings = get_settings()
    if settings.app_env not in _ALLOWED:
        raise SystemExit(f"拒絕：APP_ENV={settings.app_env}")
    if os.environ.get("ALLOW_DEV_SEED") != "true":
        raise SystemExit("需 ALLOW_DEV_SEED=true")
    # 安全閘以「URL 的資料庫名精確比對」為準——子字串比對會被密碼含該字樣或
    # lucamp_sim_restore 之類的鄰居庫騙過，灌錯庫（Codex 第二輪 P1）。
    db_name = str(settings.database_url).rsplit("/", 1)[-1].split("?", 1)[0]
    if db_name != "lucamp_sim":
        raise SystemExit(f"安全閘：sim_180d 只允許對 lucamp_sim 執行（目前 db={db_name}）")

    sm = get_sessionmaker()
    async with sm() as session:
        store = await session.scalar(select(Store).where(Store.id == 1))
        manager = await session.scalar(select(User).where(User.username == "dev-manager"))
        clerk = await session.scalar(select(User).where(User.username == "dev-clerk"))
        if store is None or manager is None:
            raise SystemExit("請先跑 seed_dev_store 與 seed_dev_user")
        sim = Sim(session, store.id, manager.id, (clerk or manager).id)
        cols = await _shiftable_columns(session)
        schedule = build_schedule(DAYS, SEED)
        await _snapshot_watermarks(sim, cols)
        await _bootstrap(sim)
        await _shift_day(sim, cols, _day_start(0) - timedelta(days=1))
        for plan in schedule:
            await _run_day(sim, plan, cols)
            if plan.day_index % 20 == 0:
                print(
                    f"day {plan.day_index}/{DAYS} sales={sim.stats['sales']} "
                    f"buyouts={sim.stats['buyouts']} affidavits={sim.stats['affidavits']}"
                )
        # 仍進行中的活動：起始時間回填為模擬歷史（迄未來、維持 ACTIVE 供 POS 現況展示）
        for cid, (_end, s_ago, _e) in sim.s.info.get("live_campaigns", {}).items():
            await session.execute(
                text("UPDATE campaigns SET starts_at = :s, ends_at = :e WHERE id = :cid"),
                {
                    "s": _NOW - timedelta(days=s_ago),
                    "e": _NOW + timedelta(days=10),
                    "cid": cid,
                },
            )
        await session.commit()
        await _write_manifest(sim)


if __name__ == "__main__":
    asyncio.run(_main())
