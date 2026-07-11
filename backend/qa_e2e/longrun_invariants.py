"""Layer B — 長期營運後不變量與帳本一致性驗證（QA, read-only）。

對 DATABASE_URL 指向的 DB（lucamp_e2e）執行 B0–B13 斷言，逐條印出 PASS/FAIL 與細節。
**不修改任何資料**；發現的違規即為一個 finding。以服務層既有公式（cashdrawer expected）
重算現金，避免另立口徑漂移。

執行：
    cd backend && DATABASE_URL=...lucamp_e2e uv run python -m qa_e2e.longrun_invariants
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from sqlalchemy import select, text

import app.main  # noqa: F401  # 觸發模型註冊
from app.core.db import get_sessionmaker
from app.core.money import round_ntd
from app.modules.cashdrawer.models import CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.shared.enums import CashSessionStatus

_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))


async def b0_data_volume_thresholds(session) -> None:
    """B0：確認本 QA DB 達到一年級距與指定資料量門檻。"""
    members = (
        await session.execute(text("SELECT COUNT(*) FROM contacts WHERE 'MEMBER'=ANY(roles)"))
    ).scalar_one()
    sales_count, span_days = (
        await session.execute(
            text(
                """
        SELECT COUNT(*) FILTER (WHERE invoice_status <> 'VOID') AS sales_count,
               COALESCE(EXTRACT(EPOCH FROM (MAX(created_at)-MIN(created_at))) / 86400, 0)
                 AS span_days
        FROM sales
        """
            )
        )
    ).one()
    purchase_orders = (
        await session.execute(text("SELECT COUNT(*) FROM purchase_orders"))
    ).scalar_one()
    campaigns = (await session.execute(text("SELECT COUNT(*) FROM campaigns"))).scalar_one()
    credit_entries = (
        await session.execute(text("SELECT COUNT(*) FROM store_credit_ledger"))
    ).scalar_one()
    menu_lines = (
        await session.execute(text("SELECT COUNT(*) FROM sale_lines WHERE line_type='MENU'"))
    ).scalar_one()
    discounted_lines = (
        await session.execute(text("SELECT COUNT(*) FROM sale_lines WHERE campaign_id IS NOT NULL"))
    ).scalar_one()

    check("B0 會員數 >= 2200", members >= 2200, f"members={members}")
    check("B0 未作廢銷售 >= 3300", sales_count >= 3300, f"sales={sales_count}")
    check("B0 銷售時間跨度 >= 350 天", Decimal(span_days) >= Decimal(350), f"days={span_days:.1f}")
    check("B0 採購單 >= 80", purchase_orders >= 80, f"purchase_orders={purchase_orders}")
    check("B0 活動 >= 6", campaigns >= 6, f"campaigns={campaigns}")
    check("B0 購物金分錄 >= 600", credit_entries >= 600, f"ledger={credit_entries}")
    check("B0 餐飲銷售行 >= 300", menu_lines >= 300, f"menu_lines={menu_lines}")
    check("B0 活動折扣銷售行 > 0", discounted_lines > 0, f"discounted_lines={discounted_lines}")


async def b1_serialized_no_double_sell(session) -> None:
    """B1：任一序號品在未作廢銷售中至多被售一次；status=SOLD ⇔ 恰一筆未作廢售出行。"""
    rows = (
        await session.execute(
            text(
                """
        SELECT sl.serialized_item_id, COUNT(*) AS n
        FROM sale_lines sl JOIN sales s ON s.id = sl.sale_id
        WHERE sl.line_type = 'SERIALIZED' AND s.invoice_status <> 'VOID'
        GROUP BY sl.serialized_item_id HAVING COUNT(*) > 1
        """
            )
        )
    ).all()
    check("B1 序號品無重複售出", len(rows) == 0, f"重複售出 item_id={[r[0] for r in rows]}")

    # status 與售出行對齊
    mism = (
        await session.execute(
            text(
                """
        SELECT si.id, si.status, COALESCE(cnt.n,0) AS sold_lines
        FROM serialized_items si
        LEFT JOIN (
          SELECT sl.serialized_item_id AS iid, COUNT(*) n
          FROM sale_lines sl JOIN sales s ON s.id = sl.sale_id
          WHERE sl.line_type='SERIALIZED' AND s.invoice_status <> 'VOID'
          GROUP BY sl.serialized_item_id
        ) cnt ON cnt.iid = si.id
        WHERE (si.status='SOLD') <> (COALESCE(cnt.n,0) = 1)
        """
            )
        )
    ).all()
    check(
        "B1 序號品 status=SOLD ⇔ 恰一未作廢售出",
        len(mism) == 0,
        f"不一致 {len(mism)} 筆，例 {mism[:5]}",
    )


async def b2_consignment_math(session) -> None:
    """B2：每筆寄售結算 commission/payout 算術正確且 gross 對齊售出行金額。"""
    rows = (
        await session.execute(
            text(
                """
        SELECT cs.id, cs.gross, cs.commission_pct, cs.commission_amount, cs.payout_amount
        FROM consignment_settlements cs
        """
            )
        )
    ).all()
    bad_math = []
    for sid, gross, pct, comm, payout in rows:
        exp_comm = round_ntd(Decimal(gross) * Decimal(pct) / Decimal(100))
        exp_payout = Decimal(gross) - exp_comm
        if Decimal(comm) != exp_comm or Decimal(payout) != exp_payout:
            bad_math.append((sid, gross, pct, comm, payout, str(exp_comm), str(exp_payout)))
    check("B2 寄售抽成/應付算術", len(bad_math) == 0, f"錯算 {bad_math[:5]}")

    # gross 應等於該寄售品在該銷售中的成交行金額
    bad_gross = (
        await session.execute(
            text(
                """
        SELECT cs.id, cs.gross, sl.line_total
        FROM consignment_settlements cs
        JOIN sale_lines sl
          ON sl.sale_id = cs.sale_id AND sl.serialized_item_id = cs.serialized_item_id
         AND sl.line_type='SERIALIZED'
        WHERE cs.gross <> sl.line_total
        """
            )
        )
    ).all()
    check("B2 寄售 gross=成交行金額", len(bad_gross) == 0, f"不符 {bad_gross[:5]}")


async def b3_tender_and_cash_links(session) -> None:
    """B3：CASH tender 對應 SALE_IN；STORE_CREDIT tender 對應 ledger DEBIT。"""
    cash_bad = (
        await session.execute(
            text(
                """
        SELECT s.id, COALESCE(ct.cash_amount,0) AS cash_tender,
               COALESCE(cm.cash_movement,0) AS cash_movement
        FROM sales s
        LEFT JOIN (
          SELECT sale_id, SUM(amount) AS cash_amount
          FROM sale_tenders WHERE tender_type='CASH' GROUP BY sale_id
        ) ct ON ct.sale_id=s.id
        LEFT JOIN (
          SELECT ref_id AS sale_id, SUM(amount) AS cash_movement
          FROM cash_movements
          WHERE type='SALE_IN' AND ref_type='sale'
          GROUP BY ref_id
        ) cm ON cm.sale_id=s.id
        WHERE COALESCE(ct.cash_amount,0) <> COALESCE(cm.cash_movement,0)
        """
            )
        )
    ).all()
    check("B3 CASH tender = SALE_IN 現金異動", len(cash_bad) == 0, f"不符 {cash_bad[:5]}")

    credit_bad = (
        await session.execute(
            text(
                """
        SELECT s.id, COALESCE(st.credit_amount,0) AS credit_tender,
               COALESCE(-ledger.signed_amount,0) AS ledger_debit
        FROM sales s
        LEFT JOIN (
          SELECT sale_id, SUM(amount) AS credit_amount
          FROM sale_tenders WHERE tender_type='STORE_CREDIT' GROUP BY sale_id
        ) st ON st.sale_id=s.id
        LEFT JOIN store_credit_ledger ledger
          ON ledger.source_type='SALE' AND ledger.entry_type='DEBIT'
         AND ledger.source_id=s.id AND ledger.store_id=s.store_id
        WHERE COALESCE(st.credit_amount,0) <> COALESCE(-ledger.signed_amount,0)
        """
            )
        )
    ).all()
    check(
        "B3 STORE_CREDIT tender = ledger SALE/DEBIT",
        len(credit_bad) == 0,
        f"不符 {credit_bad[:5]}",
    )


async def b4_cash_reconcile(session) -> None:
    """B4：每個 CLOSED session 的 expected = 服務層公式重算；variance = counted − expected。"""
    svc = CashDrawerService(session)
    sessions = (
        (
            await session.execute(
                select(CashSession).where(CashSession.status == CashSessionStatus.CLOSED)
            )
        )
        .scalars()
        .all()
    )
    bad = []
    for cs in sessions:
        recomputed = await svc.expected_amount(cs)
        stored = cs.expected_amount
        var_ok = cs.variance is None or (
            cs.counted_amount is not None and cs.variance == cs.counted_amount - stored
        )
        if stored != recomputed or not var_ok:
            bad.append(
                (cs.id, str(stored), str(recomputed), str(cs.counted_amount), str(cs.variance))
            )
    check(
        "B4 現金 expected 與服務公式同源 + variance 正確",
        len(bad) == 0,
        f"CLOSED sessions={len(sessions)}, 不符 {bad[:5]}",
    )


async def b6_bulk_lot(session) -> None:
    """B6：散裝批 remaining/status/售出量三者守恆。"""
    rows = (
        await session.execute(
            text(
                """
        SELECT bl.id, bl.total_qty, bl.remaining_qty, bl.status,
               COALESCE(sold.q,0) AS sold_qty
        FROM bulk_lots bl
        LEFT JOIN (
          SELECT sl.bulk_lot_id AS lid, SUM(sl.qty) q
          FROM sale_lines sl JOIN sales s ON s.id=sl.sale_id
          WHERE sl.line_type='BULK_LOT' AND s.invoice_status <> 'VOID'
          GROUP BY sl.bulk_lot_id
        ) sold ON sold.lid = bl.id
        """
            )
        )
    ).all()
    neg = [r for r in rows if r[2] < 0 or r[2] > r[1]]
    status_bad = [r for r in rows if (r[3] == "SOLD_OUT") != (r[2] == 0)]
    qty_bad = [r for r in rows if r[4] + r[2] != r[1]]
    check("B6 散裝 remaining ∈ [0,total]", len(neg) == 0, f"越界 {neg[:5]}")
    check("B6 散裝 SOLD_OUT ⇔ remaining=0", len(status_bad) == 0, f"狀態不符 {status_bad[:5]}")
    check("B6 散裝 售出qty+remaining=total", len(qty_bad) == 0, f"守恆破 {qty_bad[:5]}")


async def b8_store_credit(session) -> None:
    """B8：每個帳戶 balance = Σ ledger.signed_amount；balance_after 滾動正確且非負。"""
    accts = (
        await session.execute(
            text("SELECT id, store_id, contact_id, balance FROM store_credit_accounts")
        )
    ).all()
    bad_balance = []
    for _aid, store_id, contact_id, balance in accts:
        s = (
            await session.execute(
                text(
                    "SELECT COALESCE(SUM(signed_amount),0) FROM store_credit_ledger "
                    "WHERE store_id=:s AND contact_id=:c"
                ),
                {"s": store_id, "c": contact_id},
            )
        ).scalar_one()
        if Decimal(s) != Decimal(balance):
            bad_balance.append((store_id, contact_id, str(balance), str(s)))
    check("B8 購物金 account.balance = Σledger", len(bad_balance) == 0, f"不符 {bad_balance[:5]}")

    neg = (
        await session.execute(
            text("SELECT COUNT(*) FROM store_credit_ledger WHERE balance_after < 0")
        )
    ).scalar_one()
    check("B8 購物金 balance_after 恆非負", neg == 0, f"負餘額分錄 {neg} 筆")

    # 滾動一致：依 contact、id 序，balance_after 應等於前一筆 + signed_amount
    roll_bad = (
        await session.execute(
            text(
                """
        WITH l AS (
          SELECT contact_id, id, signed_amount, balance_after,
                 SUM(signed_amount) OVER (PARTITION BY store_id, contact_id ORDER BY id) AS running
          FROM store_credit_ledger
        )
        SELECT contact_id, id, balance_after, running FROM l WHERE balance_after <> running
        """
            )
        )
    ).all()
    check("B8 購物金 balance_after 滾動=累積和", len(roll_bad) == 0, f"漂移 {roll_bad[:5]}")


async def b10_money_integrity(session) -> None:
    """B10：銷售稅額與 tender 對平；金額皆整數。"""
    nt = (
        await session.execute(
            text("SELECT id, subtotal, tax, total FROM sales WHERE subtotal+tax <> total")
        )
    ).all()
    check("B10 net+tax=total 不差一元", len(nt) == 0, f"不符 {nt[:5]}")

    tender_bad = (
        await session.execute(
            text(
                """
        SELECT s.id, s.total, COALESCE(SUM(t.amount),0) AS tsum
        FROM sales s LEFT JOIN sale_tenders t ON t.sale_id=s.id
        WHERE s.invoice_status <> 'VOID'
        GROUP BY s.id, s.total HAVING s.total <> COALESCE(SUM(t.amount),0)
        """
            )
        )
    ).all()
    check("B10 非作廢單 Σtender=total", len(tender_bad) == 0, f"不對平 {tender_bad[:5]}")


async def b11_food_store_credit_controls(session) -> None:
    """B11：餐飲不得以購物金折抵；餐飲行不得套活動折扣。"""
    food_credit_bad = (
        await session.execute(
            text(
                """
        WITH food AS (
          SELECT sale_id, SUM(line_total) AS food_subtotal
          FROM sale_lines WHERE line_type='MENU' GROUP BY sale_id
        ), credit AS (
          SELECT sale_id, SUM(amount) AS credit_amount
          FROM sale_tenders WHERE tender_type='STORE_CREDIT' GROUP BY sale_id
        )
        SELECT s.id, s.total, food.food_subtotal, credit.credit_amount
        FROM sales s
        JOIN food ON food.sale_id=s.id
        JOIN credit ON credit.sale_id=s.id
        WHERE credit.credit_amount > s.total - food.food_subtotal
        """
            )
        )
    ).all()
    check(
        "B11 餐飲不得以購物金折抵",
        len(food_credit_bad) == 0,
        f"違規 {food_credit_bad[:5]}",
    )

    menu_discount_bad = (
        await session.execute(
            text(
                """
        SELECT id, sale_id, original_unit_price, discount_amount, campaign_id
        FROM sale_lines
        WHERE line_type='MENU'
          AND (original_unit_price IS NOT NULL OR discount_amount <> 0 OR campaign_id IS NOT NULL)
        """
            )
        )
    ).all()
    check("B11 餐飲不套門市活動折扣", len(menu_discount_bad) == 0, f"違規 {menu_discount_bad[:5]}")


async def b12_campaign_discounts(session) -> None:
    """B12：活動折扣留痕正確；同店至多一個 ACTIVE 活動。"""
    active_bad = (
        await session.execute(
            text(
                """
        SELECT store_id, COUNT(*)
        FROM campaigns
        WHERE status='ACTIVE'
        GROUP BY store_id HAVING COUNT(*) > 1
        """
            )
        )
    ).all()
    check("B12 同店至多一個 ACTIVE 活動", len(active_bad) == 0, f"違規 {active_bad[:5]}")

    discount_bad = (
        await session.execute(
            text(
                """
        SELECT id, sale_id, unit_price, original_unit_price, qty, discount_amount, campaign_id
        FROM sale_lines
        WHERE campaign_id IS NOT NULL
          AND (
            original_unit_price IS NULL
            OR discount_amount <= 0
            OR discount_amount <> (original_unit_price - unit_price) * qty
            OR unit_price > original_unit_price
          )
        """
            )
        )
    ).all()
    check("B12 活動折扣留痕與成交價一致", len(discount_bad) == 0, f"違規 {discount_bad[:5]}")


async def b13_audit_presence(session) -> None:
    """B13：金流/活動級敏感操作有稽核紀錄，且稽核 JSON 不含身分證明文。"""
    counts = dict(
        (
            await session.execute(
                text(
                    """
        SELECT action, COUNT(*)
        FROM audit_log
        WHERE action IN (
          'CAMPAIGN_CREATE', 'CAMPAIGN_ACTIVATE', 'CAMPAIGN_END', 'CAMPAIGN_CANCEL',
          'STORE_CREDIT_ADJUST', 'VOID_SALE'
        )
        GROUP BY action
        """
                )
            )
        ).all()
    )
    required = ["CAMPAIGN_CREATE", "CAMPAIGN_ACTIVATE", "CAMPAIGN_END", "STORE_CREDIT_ADJUST"]
    missing = [name for name in required if counts.get(name, 0) == 0]
    check("B13 活動/購物金敏感操作有 audit_log", not missing, f"缺少 {missing}")

    plaintext_ids = (
        await session.execute(
            text(
                """
        SELECT id, action
        FROM audit_log
        WHERE COALESCE(before::text,'') ~ '[A-Z][12][0-9]{8}'
           OR COALESCE(after::text,'') ~ '[A-Z][12][0-9]{8}'
        LIMIT 5
        """
            )
        )
    ).all()
    check(
        "B13 audit_log 不含身分證明文",
        len(plaintext_ids) == 0,
        f"疑似 {plaintext_ids[:5]}",
    )


async def data_volume(session) -> None:
    for tbl in [
        "sales",
        "sale_lines",
        "sale_tenders",
        "serialized_items",
        "bulk_lots",
        "consignment_settlements",
        "cash_sessions",
        "cash_movements",
        "store_credit_ledger",
        "contacts",
        "campaigns",
    ]:
        n = (await session.execute(text(f"SELECT COUNT(*) FROM {tbl}"))).scalar_one()
        print(f"    {tbl:28} {n}")


async def main() -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        print("=== 資料量 ===")
        await data_volume(session)
        print("\n=== Layer B 不變量 ===")
        await b0_data_volume_thresholds(session)
        await b1_serialized_no_double_sell(session)
        await b2_consignment_math(session)
        await b3_tender_and_cash_links(session)
        await b4_cash_reconcile(session)
        await b6_bulk_lot(session)
        await b8_store_credit(session)
        await b10_money_integrity(session)
        await b11_food_store_credit_controls(session)
        await b12_campaign_discounts(session)
        await b13_audit_presence(session)

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"\n=== 結果：{passed}/{total} PASS ===")
    if passed != total:
        print("FAILURES:")
        for name, ok, detail in _results:
            if not ok:
                print(f"  - {name}: {detail}")


if __name__ == "__main__":
    asyncio.run(main())
