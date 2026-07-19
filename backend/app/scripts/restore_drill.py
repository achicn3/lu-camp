"""還原演練（docs/31 §6.1，使用者指示）：對真資料實跑「備份→還原→逐功能比對」。

流程：
  1) 對來源庫（預設 lucamp_sim）跑一次真備份（pg_dump→AES→R2，走 app 的 SubprocessR2Backend）。
  2) 下載→解密→還原到 throwaway 庫（走 app 的 SubprocessR2RestoreBackend）。
  3) 對「來源庫 vs 還原庫」逐功能執行同一批查詢（交易/現金/會員PII/庫存/簽署/購物金/盤點/
     寄售/採購/發票/報表/稽核），**每個功能的結果都必須一致**才算通過。
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
    ("庫存-序號品數", "SELECT count(*) FROM serialized_items"),
    ("庫存-散裝餘量合計", "SELECT COALESCE(SUM(remaining_qty),0) FROM bulk_lots"),
    ("簽署-任務數", "SELECT count(*) FROM signature_tasks"),
    (
        "簽署-簽名BYTEA雜湊",
        "SELECT md5(COALESCE(string_agg(md5(signature_image), ',' ORDER BY id),''))"
        " FROM signature_tasks WHERE signature_image IS NOT NULL",
    ),
    ("購物金-帳本筆數", "SELECT count(*) FROM store_credit_ledger"),
    ("購物金-淨額合計", "SELECT COALESCE(SUM(signed_amount),0) FROM store_credit_ledger"),
    ("盤點-單數", "SELECT count(*) FROM stocktakes"),
    ("盤點-明細數", "SELECT count(*) FROM stocktake_lines"),
    ("寄售-結算數", "SELECT count(*) FROM consignment_settlements"),
    ("採購-單數", "SELECT count(*) FROM purchase_orders"),
    ("採購-收貨數", "SELECT count(*) FROM goods_receipts"),
    ("發票-筆數", "SELECT count(*) FROM invoices"),
    ("發票-折讓數", "SELECT count(*) FROM invoice_allowances"),
    ("稽核-筆數", "SELECT count(*) FROM audit_log"),
]


def _url_for(db_name: str) -> str:
    base = os.environ["DATABASE_URL"]
    return make_url(base).set(database=db_name).render_as_string(hide_password=False)


async def _snapshot(db_name: str) -> dict[str, str]:
    engine = create_async_engine(_url_for(db_name))
    out: dict[str, str] = {}
    try:
        async with engine.connect() as conn:
            for label, sql in FEATURE_CHECKS:
                try:
                    val = await conn.scalar(text(sql))
                    out[label] = str(val)
                except Exception as exc:  # 該功能表缺損＝該項失敗
                    out[label] = f"ERR:{exc.__class__.__name__}"
    finally:
        await engine.dispose()
    return out


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

    print(f"[1/4] 備份來源庫 {SOURCE_DB} → R2 …")
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    artifact = await backup_backend.create_and_upload(db_name=SOURCE_DB, stamp=stamp)
    print(f"      上傳 {artifact.r2_key}（{artifact.size_bytes} bytes）")

    print("[2/4] 擷取來源庫逐功能快照 …")
    before = await _snapshot(SOURCE_DB)

    target = default_restore_db_name()
    print(f"[3/4] 還原到 throwaway 庫 {target} …")
    await restore_backend.fetch_and_restore(r2_key=artifact.r2_key, target_db=target)
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


raise SystemExit(asyncio.run(main()))
