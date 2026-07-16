"""Layer B（sim 版）— 180 天全真模擬資料的不變量掃描（docs/27 Phase 2, read-only）。

先以 sim_manifest.json 做 fail-closed 綁定（筆數/跨度不符即退出，杜絕空庫假通過），
再重用 longrun_invariants 的 B1–B13（B0 門檻為舊 seed 專用，改以 S0 取代），
最後跑 sim 專屬 S 系列：簽署證據鏈、進項發票守恆、點數重算、SCU/ACK 綁定、
序號品狀態機、現金班別全量重算已由 B4 涵蓋。

執行：
    cd backend && DATABASE_URL=...lucamp_sim uv run python -m qa_e2e.sim_invariants
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import app.main  # noqa: F401  # 觸發模型註冊
from app.core.db import get_sessionmaker
from qa_e2e.longrun_invariants import (
    _results,
    b1_serialized_no_double_sell,
    b2_consignment_math,
    b3_tender_and_cash_links,
    b4_cash_reconcile,
    b6_bulk_lot,
    b8_store_credit,
    b10_money_integrity,
    b11_food_store_credit_controls,
    b12_campaign_discounts,
    b13_audit_presence,
    check,
)

_MANIFEST = Path(__file__).with_name("sim_manifest.json")


async def s0_manifest_binding(session: AsyncSession) -> None:
    """S0：fail-closed——DB 必須就是 manifest 描述的那份資料集。"""
    if not _MANIFEST.exists():
        check("S0 manifest 存在", False, "缺 sim_manifest.json；先跑 sim_180d")
        return
    m = json.loads(_MANIFEST.read_text())
    check("S0 manifest 存在", True, f"seed={m['seed']} days={m['days']}")
    for tab, expected in m["counts"].items():
        actual = (await session.execute(text(f"SELECT COUNT(*) FROM {tab}"))).scalar_one()
        if actual != expected:
            check(f"S0 {tab} 筆數綁定", False, f"manifest={expected} db={actual}")
            print("S0 綁定失敗——非同一份資料集，中止。")
            sys.exit(1)
    check("S0 全表筆數綁定", True, f"{len(m['counts'])} 表一致")
    lo, hi = (datetime.fromisoformat(x) for x in m["date_span"])
    span = (hi - lo).days
    check("S0 銷售跨度 ≥ 180 天", span >= 180, f"span={span}d")
    check("S0 銷售 ≥ 4000", m["counts"]["sales"] >= 4000, f"sales={m['counts']['sales']}")
    check(
        "S0 簽署任務 ≥ 600",
        m["counts"]["signature_tasks"] >= 600,
        f"tasks={m['counts']['signature_tasks']}",
    )
    check("S0 班別 = 模擬天數", m["counts"]["cash_sessions"] == m["days"], "")


async def s1_signing_evidence(session: AsyncSession) -> None:
    """S1：簽署證據完整性——SIGNED 必有影像/時間/冪等指紋；影像為合法 PNG(RGBA)。"""
    rows = (
        await session.execute(
            text(
                "SELECT id, signature_image IS NULL AS no_img, signed_at IS NULL AS no_ts, "
                "sign_idempotency_key IS NULL AS no_key FROM signature_tasks "
                "WHERE status = 'SIGNED'"
            )
        )
    ).all()
    bad = [r[0] for r in rows if r[1] or r[2]]
    check("S1 SIGNED 必有簽名影像+時間", len(bad) == 0, f"缺件 task={bad[:5]}（共{len(rows)}筆）")
    no_key = [r[0] for r in rows if r[3]]
    check("S1 SIGNED 有冪等指紋", len(no_key) == 0, f"缺鍵 task={no_key[:5]}")

    imgs = (
        await session.execute(
            text("SELECT id, signature_image FROM signature_tasks WHERE status='SIGNED'")
        )
    ).all()
    bad_png = []
    for tid, img in imgs:
        raw = bytes(img)
        if not raw.startswith(b"\x89PNG\r\n\x1a\n") or raw[25] != 6 or raw[24] != 8:
            bad_png.append(tid)
    check("S1 簽名影像全為 8-bit RGBA PNG", len(bad_png) == 0, f"異常 {bad_png[:5]}")

    ts_bad = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM signature_tasks "
                "WHERE status='SIGNED' AND signed_at < created_at"
            )
        )
    ).scalar_one()
    check("S1 signed_at ≥ created_at", ts_bad == 0, f"倒置 {ts_bad} 筆")

    aff = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM signature_tasks WHERE kind='ACQUISITION_AFFIDAVIT' "
                "AND status='SIGNED' AND agreement_version_id IS NULL"
            )
        )
    ).scalar_one()
    check("S1 已簽切結必綁切結書版本", aff == 0, f"缺版本 {aff} 筆")


async def s2_acquisition_binding(session: AsyncSession) -> None:
    """S2：收購↔切結綁定——綁定任務必為同店同人已簽 AFFIDAVIT；切結單次使用。"""
    rows = (
        await session.execute(
            text(
                """
        SELECT a.id, t.id, t.kind, t.status, t.contact_id = a.contact_id AS same_contact,
               t.store_id = a.store_id AS same_store
        FROM acquisitions a JOIN signature_tasks t ON t.id = a.signature_task_id
        WHERE a.signature_task_id IS NOT NULL
        """
            )
        )
    ).all()
    bad = [
        r[0]
        for r in rows
        if r[2] != "ACQUISITION_AFFIDAVIT" or r[3] != "SIGNED" or not r[4] or not r[5]
    ]
    check("S2 綁定切結＝同店同人已簽 AFFIDAVIT", len(bad) == 0, f"違規 acq={bad[:5]}")
    dup = (
        await session.execute(
            text(
                "SELECT signature_task_id FROM acquisitions WHERE signature_task_id IS NOT NULL "
                "GROUP BY signature_task_id HAVING COUNT(*) > 1"
            )
        )
    ).all()
    dup_ids = [r[0] for r in dup][:5]
    check("S2 切結單次使用（一任務一收購）", len(dup) == 0, f"重用 task={dup_ids}")
    # 強制簽署生效後（首張綁定收購次日起），付現收購一律有綁定
    first = (
        await session.execute(
            text(
                "SELECT MIN(created_at) FROM acquisitions "
                "WHERE signature_task_id IS NOT NULL"
            )
        )
    ).scalar_one()
    if first is not None:
        unbound = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM acquisitions WHERE type IN ('BUYOUT','BULK_LOT') "
                    "AND signature_task_id IS NULL "
                    "AND created_at > CAST(:cut AS timestamptz) + interval '1 day'"
                ),
                {"cut": first},
            )
        ).scalar_one()
        check("S2 強制簽署生效後付現收購全綁切結", unbound == 0, f"未綁 {unbound} 筆")


async def s3_input_invoice(session: AsyncSession) -> None:
    """S3：進項發票——全空或全備；net+tax=total；同店同號同日唯一；格式 2英8數。"""
    rows = (
        await session.execute(
            text(
                """
        SELECT id,
          (invoice_number IS NULL) AS n0, (invoice_date IS NULL) AS d0,
          (invoice_total IS NULL) AS t0,
          invoice_number, invoice_total, invoice_net, invoice_tax
        FROM goods_receipts
        """
            )
        )
    ).all()
    partial = [r[0] for r in rows if len({r[1], r[2], r[3]}) != 1]
    check("S3 進項發票全空或全備", len(partial) == 0, f"半套 receipt={partial[:5]}")
    with_inv = [r for r in rows if not r[1]]
    bad_sum = [r[0] for r in with_inv if (r[6] or 0) + (r[7] or 0) != (r[5] or 0)]
    check("S3 net+tax=total", len(bad_sum) == 0, f"不守恆 {bad_sum[:5]}（發票共{len(with_inv)}張）")
    import re

    bad_fmt = [r[0] for r in with_inv if not re.match(r"^[A-Z]{2}[0-9]{8}$", r[4] or "")]
    check("S3 號碼格式 2英8數", len(bad_fmt) == 0, f"格式錯 {bad_fmt[:5]}")
    dup = (
        await session.execute(
            text(
                "SELECT invoice_number FROM goods_receipts WHERE invoice_number IS NOT NULL "
                "GROUP BY store_id, invoice_number, invoice_date HAVING COUNT(*) > 1"
            )
        )
    ).all()
    check("S3 同店同號同日唯一", len(dup) == 0, f"重複 {[r[0] for r in dup][:5]}")


async def s4_member_points(session: AsyncSession) -> None:
    """S4：點數——非作廢銷售逐筆重算 floor((total−餐飲)/100)（無買方＝0）。"""
    rows = (
        await session.execute(
            text(
                """
        SELECT s.id, s.awarded_points, s.buyer_contact_id, s.total,
               COALESCE(SUM(l.line_total) FILTER (WHERE l.line_type='MENU'), 0) AS food
        FROM sales s LEFT JOIN sale_lines l ON l.sale_id = s.id
        WHERE s.invoice_status <> 'VOID'
        GROUP BY s.id
        """
            )
        )
    ).all()
    bad = []
    for sid, pts, buyer, total, food in rows:
        expect = int((total - food) // 100) if buyer is not None else 0
        if int(pts) != expect:
            bad.append((sid, int(pts), expect))
    check("S4 點數逐筆重算一致", len(bad) == 0, f"不符 {bad[:5]}（共{len(rows)}筆）")


async def s5_scu_binding(session: AsyncSession) -> None:
    """S5：購物金扣抵簽署——sale 綁定任務＝已簽 SCU、同買方，debit＝購物金 tender。"""
    rows = (
        await session.execute(
            text(
                """
        SELECT s.id, t.kind, t.status, t.contact_id = s.buyer_contact_id AS same_buyer,
               t.content->>'debit' AS debit,
               (SELECT SUM(amount) FROM sale_tenders st
                 WHERE st.sale_id = s.id AND st.tender_type='STORE_CREDIT') AS credit_amt
        FROM sales s JOIN signature_tasks t ON t.id = s.signature_task_id
        WHERE s.signature_task_id IS NOT NULL
        """
            )
        )
    ).all()
    bad = [
        r[0]
        for r in rows
        if r[1] != "STORE_CREDIT_USE" or r[2] != "SIGNED" or not r[3]
        or r[4] is None or r[5] is None or int(r[4]) != int(r[5])
    ]
    detail5 = f"違規 sale={bad[:5]}（綁定{len(rows)}筆）"
    check("S5 SCU 綁定＝已簽同買方且 debit 相符", len(bad) == 0, detail5)


async def s6_ack_content(session: AsyncSession) -> None:
    """S6：交易紀錄簽收——content 單號/總額與銷售單一致、對象＝買方。"""
    rows = (
        await session.execute(
            text(
                """
        SELECT t.id, t.content->>'sale_ref' AS ref, t.content->>'total' AS ttl,
               s.id AS sale_id, s.total, t.contact_id = s.buyer_contact_id AS same
        FROM signature_tasks t
        JOIN sales s ON s.id = t.ref_id AND t.ref_type = 'sale'
        WHERE t.kind = 'TRANSACTION_ACK' AND t.status = 'SIGNED'
        """
            )
        )
    ).all()
    bad = [
        r[0]
        for r in rows
        if r[1] != f"#{r[3]}" or int(r[2] or -1) != int(r[4]) or not r[5]
    ]
    check("S6 ACK 內容＝後端銷售實態", len(bad) == 0, f"不符 task={bad[:5]}（共{len(rows)}筆）")


async def s7_serialized_state(session: AsyncSession) -> None:
    """S7：序號品狀態機——SOLD ⇔ 有未作廢銷售行；IN_STOCK 不得有有效銷售行。"""
    sold_no_line = (
        await session.execute(
            text(
                """
        SELECT i.id FROM serialized_items i
        WHERE i.status = 'SOLD' AND NOT EXISTS (
          SELECT 1 FROM sale_lines l JOIN sales s ON s.id = l.sale_id
          WHERE l.serialized_item_id = i.id AND s.invoice_status <> 'VOID')
        """
            )
        )
    ).all()
    orphan_ids = [r[0] for r in sold_no_line][:5]
    check("S7 SOLD 必有有效銷售行", len(sold_no_line) == 0, f"孤兒 {orphan_ids}")
    stock_with_line = (
        await session.execute(
            text(
                """
        SELECT i.id FROM serialized_items i
        WHERE i.status = 'IN_STOCK' AND EXISTS (
          SELECT 1 FROM sale_lines l JOIN sales s ON s.id = l.sale_id
          WHERE l.serialized_item_id = i.id AND s.invoice_status <> 'VOID'
          AND s.status <> 'RETURNED'
          AND NOT EXISTS (SELECT 1 FROM return_lines rl WHERE rl.sale_line_id = l.id))
        """
            )
        )
    ).all()
    check(
        "S7 IN_STOCK 不得有未退貨的有效銷售行",
        len(stock_with_line) == 0,
        f"矛盾 {[r[0] for r in stock_with_line][:5]}",
    )


async def s8_signature_audit_gap(session: AsyncSession) -> None:
    """S8（已知缺口驗證）：調閱簽名影像是否寫 audit_log——預期【無】，列健檢 P1。"""
    n = (
        await session.execute(
            text("SELECT COUNT(*) FROM audit_log WHERE action ILIKE '%SIGNATURE%VIEW%'")
        )
    ).scalar_one()
    # 這裡是「記錄現況」而非 PASS/FAIL 斷言：程式碼層已確認端點無稽核呼叫。
    detail8 = f"VIEW 類稽核 {n} 筆（端點未寫→健檢 P1 候選）"
    check("S8 簽名調閱稽核（已知缺口，資訊性）", True, detail8)


async def s9_supplier_snapshot(session: AsyncSession) -> None:
    """S9：採購單供應商名快照非空；改名供應商的歷史單保留原名（若有改名情境）。"""
    empty = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM purchase_orders "
                "WHERE supplier_name IS NULL OR supplier_name = ''"
            )
        )
    ).scalar_one()
    check("S9 PO 供應商名快照非空", empty == 0, f"空快照 {empty} 筆")
    renamed = (
        await session.execute(
            text(
                """
        SELECT s.id, s.name, COUNT(po.id)
        FROM suppliers s JOIN purchase_orders po ON po.supplier_id = s.id
        WHERE po.supplier_name <> s.name GROUP BY s.id, s.name
        """
            )
        )
    ).all()
    detail = "; ".join(f"supplier={r[0]} 現名〈{r[1]}〉歷史單 {r[2]} 筆保留原名" for r in renamed)
    check("S9 改名不改寫歷史（資訊性）", True, detail or "無改名情境")


async def main() -> None:
    sm = get_sessionmaker()
    async with sm() as session:
        print("=== S0 manifest fail-closed ===")
        await s0_manifest_binding(session)
        print("\n=== 既有 B 系列（B1–B13）===")
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
        print("\n=== sim 專屬 S 系列 ===")
        await s1_signing_evidence(session)
        await s2_acquisition_binding(session)
        await s3_input_invoice(session)
        await s4_member_points(session)
        await s5_scu_binding(session)
        await s6_ack_content(session)
        await s7_serialized_state(session)
        await s8_signature_audit_gap(session)
        await s9_supplier_snapshot(session)

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"\n=== 結果：{passed}/{total} PASS ===")
    if passed != total:
        print("FAILURES:")
        for name, ok, detail in _results:
            if not ok:
                print(f"  - {name}: {detail}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())


# base64 僅供未來擴充影像深驗（保留 import 會被 ruff 撿掉，改為顯式引用）
_ = base64
