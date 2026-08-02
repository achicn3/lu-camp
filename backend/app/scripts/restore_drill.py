"""還原演練（docs/31 §6.1，使用者指示）：對真資料實跑「備份→還原→逐功能比對」。

流程：
  1) 對來源庫（預設 lucamp_sim）跑一次真備份（pg_dump→AES→R2，走 app 的 SubprocessR2Backend）。
  2) 下載→解密→還原到 throwaway 庫（走 app 的 SubprocessR2RestoreBackend）。
  3) 對「來源庫 vs 還原庫」逐功能執行同一批查詢（交易/現金/會員PII/庫存/簽署/購物金/盤點/
     寄售/採購/發票/稽核），**每個功能的結果都必須一致**才算通過。
  4) 印出逐功能 before/after 對照表；全部一致 exit 0，否則 exit 1。

不改任何正式資料；throwaway 庫用畢即刪。需 .env（金鑰）＋ .env.r2（R2/口令）＋ docker。
用法：uv run python -m app.scripts.restore_drill [來源庫名]
"""

import asyncio
import os
import sys
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.modules.backup.restore import alembic_head
from app.modules.backup.restore_service import default_restore_db_name
from app.modules.backup.scheduler import build_backup_backend, build_restore_backend

SOURCE_DB = sys.argv[1] if len(sys.argv) > 1 else "lucamp_sim"

# 逐功能查詢（docs/31 §6.1）：每項回一個可字串化的 scalar，來源/還原一致才算「救得回且符合預期」。
# 涵蓋筆數＋金額聚合＋BYTEA 簽名雜湊（證明不只筆數對、內容值也無損）。
FEATURE_CHECKS: list[tuple[str, str]] = [
    ("交易-筆數", "SELECT count(*) FROM sales"),
    ("交易-銷售額合計", "SELECT COALESCE(SUM(total),0) FROM sales"),
    ("交易-明細筆數", "SELECT count(*) FROM sale_lines"),
    ("交易-收款筆數", "SELECT count(*) FROM sale_tenders"),
    ("交易-作廢筆數", "SELECT count(*) FROM sales WHERE status = 'VOIDED'"),
    (
        "交易-狀態與發票狀態雜湊",
        "SELECT md5(COALESCE(string_agg(status || ':' || invoice_status, ',' ORDER BY id),''))"
        " FROM sales",
    ),
    ("退貨-單數", "SELECT count(*) FROM returns"),
    ("退貨-明細筆數", "SELECT count(*) FROM return_lines"),
    ("退貨-退款去向筆數", "SELECT count(*) FROM return_tenders"),
    (
        "退貨-退款去向金額",
        "SELECT md5(COALESCE(string_agg(tender_type || ':' || amount, ',' ORDER BY id),''))"
        " FROM return_tenders",
    ),
    ("現金-班別數", "SELECT count(*) FROM cash_sessions"),
    ("現金-異動筆數", "SELECT count(*) FROM cash_movements"),
    ("會員-筆數", "SELECT count(*) FROM contacts"),
    (
        "會員-PII密文雜湊",
        "SELECT md5(COALESCE(string_agg(national_id_enc::text, ',' ORDER BY id),''))"
        " FROM contacts",
    ),
    (
        "會員-盲索引雜湊",
        "SELECT md5(COALESCE(string_agg(national_id_blind_index, ',' ORDER BY id),''))"
        " FROM contacts",
    ),
    ("收購-單數", "SELECT count(*) FROM acquisitions"),
    ("收購-付現合計", "SELECT COALESCE(SUM(total_cash_paid),0) FROM acquisitions"),
    ("收購-作廢筆數", "SELECT count(*) FROM acquisitions WHERE voided_at IS NOT NULL"),
    ("庫存-品牌數", "SELECT count(*) FROM brands"),
    ("庫存-分類數", "SELECT count(*) FROM categories"),
    ("庫存-型號數", "SELECT count(*) FROM product_models"),
    ("庫存-序號品數", "SELECT count(*) FROM serialized_items"),
    ("庫存-一般商品現量合計", "SELECT COALESCE(SUM(quantity_on_hand),0) FROM catalog_products"),
    ("庫存-異動筆數", "SELECT count(*) FROM stock_movements"),
    ("庫存-散裝餘量合計", "SELECT COALESCE(SUM(remaining_qty),0) FROM bulk_lots"),
    # 切結書條款全文：不可變、舊簽名永遠指向舊版全文，程式碼重跑 seeder 只會有當前版 → 救不回來。
    ("簽署-條款版本數", "SELECT count(*) FROM agreement_versions"),
    (
        "簽署-條款全文雜湊",
        "SELECT md5(COALESCE(string_agg(version || ':' || md5(body), ',' ORDER BY id),''))"
        " FROM agreement_versions",
    ),
    ("簽署-任務數", "SELECT count(*) FROM signature_tasks"),
    ("簽署-事件鏈筆數", "SELECT count(*) FROM signature_task_events"),
    (
        "簽署-事件鏈雜湊",
        "SELECT md5(COALESCE(string_agg(to_status || ':' || COALESCE(reason_code,''),"
        " ',' ORDER BY id),'')) FROM signature_task_events",
    ),
    (
        "簽署-簽名BYTEA雜湊",
        "SELECT md5(COALESCE(string_agg(md5(signature_image), ',' ORDER BY id),''))"
        " FROM signature_tasks WHERE signature_image IS NOT NULL",
    ),
    ("設定-溢價率異動史筆數", "SELECT count(*) FROM premium_rate_history"),
    ("購物金-帳戶數", "SELECT count(*) FROM store_credit_accounts"),
    (
        "購物金-各會員餘額雜湊",
        "SELECT md5(COALESCE(string_agg(contact_id || ':' || balance, ',' ORDER BY id),''))"
        " FROM store_credit_accounts",
    ),
    ("購物金-帳本筆數", "SELECT count(*) FROM store_credit_ledger"),
    ("購物金-淨額合計", "SELECT COALESCE(SUM(signed_amount),0) FROM store_credit_ledger"),
    ("盤點-單數", "SELECT count(*) FROM stocktakes"),
    ("盤點-明細數", "SELECT count(*) FROM stocktake_lines"),
    ("寄售-結算數", "SELECT count(*) FROM consignment_settlements"),
    ("採購-單數", "SELECT count(*) FROM purchase_orders"),
    ("採購-收貨數", "SELECT count(*) FROM goods_receipts"),
    ("採購-明細筆數", "SELECT count(*) FROM purchase_order_lines"),
    ("採購-供應商數", "SELECT count(*) FROM suppliers"),
    ("活動-檔數", "SELECT count(*) FROM campaigns"),
    ("餐飲-菜單品項數", "SELECT count(*) FROM menu_items"),
    ("LINE Pay-交易筆數", "SELECT count(*) FROM linepay_transactions"),
    # 防重複退款的唯一依據：崩潰/回應遺失後靠它判斷「這筆是否已退過」，弄丟＝可能多退真的錢。
    ("LINE Pay-退款嘗試筆數", "SELECT count(*) FROM linepay_refund_attempts"),
    (
        "LINE Pay-退款嘗試狀態雜湊",
        "SELECT md5(COALESCE(string_agg(refund_key || ':' || status, ',' ORDER BY id),''))"
        " FROM linepay_refund_attempts",
    ),
    ("發票-筆數", "SELECT count(*) FROM invoices"),
    ("發票-折讓數", "SELECT count(*) FROM invoice_allowances"),
    (
        "發票-狀態與作廢原因雜湊",
        "SELECT md5(COALESCE(string_agg(status || ':' || COALESCE(void_reason,'-'),"
        " ',' ORDER BY id),'')) FROM invoices",
    ),
    ("發票-上傳佇列筆數", "SELECT count(*) FROM einvoice_upload_queue"),
    ("發票-回執事件筆數", "SELECT count(*) FROM einvoice_result_events"),
    ("顧客螢幕-購物車階段數", "SELECT count(*) FROM cart_sessions"),
    ("顧客螢幕-配對紀錄數", "SELECT count(*) FROM terminal_kiosk_pairings"),
    ("顧客螢幕-裝置數", "SELECT count(*) FROM kiosk_devices"),
    ("稽核-筆數", "SELECT count(*) FROM audit_log"),
]


def _url_for(db_name: str) -> str:
    base = os.environ["DATABASE_URL"]
    return make_url(base).set(database=db_name).render_as_string(hide_password=False)


async def _snapshot(db_name: str) -> dict[str, str]:
    """逐項取快照。**每項各自成一個交易**：Postgres 在同一交易內只要有一句失敗，
    後續每一句都會被拒（current transaction is aborted），若共用連線，一個缺損的欄位
    會讓它後面所有檢查一起變成假的 ERR，報告因此完全失真（實測踩過）。"""
    engine = create_async_engine(_url_for(db_name))
    out: dict[str, str] = {}
    try:
        for label, sql in FEATURE_CHECKS:
            async with engine.connect() as conn:
                try:
                    val = await conn.scalar(text(sql))
                    out[label] = str(val)
                except Exception as exc:  # 該功能表/欄位缺損＝該項失敗，但不影響其餘項目
                    out[label] = f"ERR:{exc.__class__.__name__}"
                    await conn.rollback()
    finally:
        await engine.dispose()
    return out


async def _assert_source_at_head(db_name: str) -> None:
    """來源庫必須已在 alembic head：否則等於拿一份**過期 schema** 去驗還原，
    新功能的資料完全不在檢查範圍內，卻會得到一片綠燈。"""
    engine = create_async_engine(_url_for(db_name))
    try:
        async with engine.connect() as conn:
            # 取**全部**列：多 head（分支）時 scalar() 會無序取任一列，可能剛好抽中等於 head
            # 的那一列而誤放行。缺表＝根本沒跑過 migration，訊息要講人話而不是丟 traceback。
            try:
                rows = (await conn.execute(text("SELECT version_num FROM alembic_version"))).all()
            except Exception:
                raise SystemExit(
                    f"來源庫 {db_name} 沒有 alembic_version 表——這個庫沒跑過 migration，"
                    f"不能拿來演練還原。"
                ) from None
    finally:
        await engine.dispose()
    current = {r[0] for r in rows}
    head = {alembic_head()}
    if current != head:
        raise SystemExit(
            f"來源庫 {db_name} 的 schema 版本是 {sorted(current) or '（空）'}，"
            f"不是最新的 {sorted(head)}。\n"
            f"請先跑 alembic upgrade head 再演練——否則新功能的資料不會被驗到，"
            f"卻會看到全綠而誤以為都救得回來。"
        )


async def _drop_db(db_name: str) -> None:
    # 用 postgres 維護庫連線刪 throwaway 庫（不連該庫本身）。
    engine = create_async_engine(_url_for("postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    finally:
        await engine.dispose()


async def main() -> int:
    backup_backend = build_backup_backend()
    restore_backend = build_restore_backend()
    if backup_backend is None or restore_backend is None:
        print("R2 未設定（需 source .env.r2）")
        return 2

    # 先擋在最前面：schema 過期就不必上傳（R2 成本紀律：每次演練都是一次真實上傳）。
    await _assert_source_at_head(SOURCE_DB)

    print(f"[1/4] 備份來源庫 {SOURCE_DB} → R2 …")
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    artifact = await backup_backend.create_and_upload(db_name=SOURCE_DB, stamp=stamp)
    print(f"      上傳 {artifact.r2_key}（{artifact.size_bytes} bytes）")

    print("[2/4] 擷取來源庫逐功能快照 …")
    before = await _snapshot(SOURCE_DB)

    target = default_restore_db_name()
    print(f"[3/4] 還原到 throwaway 庫 {target} …")
    await restore_backend.fetch_and_restore(
        r2_key=artifact.r2_key,
        target_db=target,
        expected_sha256=artifact.sha256,
        expected_size=artifact.size_bytes,
    )
    after = await _snapshot(target)

    print("[4/4] 逐功能比對 before（來源）vs after（還原）：\n")
    all_ok = True
    width = max(len(label) for label, _ in FEATURE_CHECKS)
    for label, _ in FEATURE_CHECKS:
        b, a = before.get(label, "?"), after.get(label, "?")
        ok = b == a and not b.startswith("ERR:")
        all_ok = all_ok and ok
        mark = "✅" if ok else "❌"
        print(f"  {mark} {label.ljust(width)}  before={b}  after={a}")

    await _drop_db(target)
    print(f"\n（throwaway 庫 {target} 已清除）")
    print("\n還原演練結果：" + ("全部功能一致 ✅ PASS" if all_ok else "有不一致 ❌ FAIL"))
    return 0 if all_ok else 1


if __name__ == "__main__":  # 匯入本模組不得執行演練（會真的備份、上傳 R2、建庫）
    raise SystemExit(asyncio.run(main()))
