"""還原驗證的涵蓋率漂移守衛。

備份是整庫 `pg_dump`，新表天然被**備份**到；但「還原已驗證」的綠燈只涵蓋
`_KEY_TABLES`（四驗抽樣）與 `FEATURE_CHECKS`（逐功能比對）點名的表。這兩份清單靠人工維護，
而人工維護一定會漂——實際發生過：備份系統合併後新增了 7 張表，兩份清單一張都沒跟上，
於是「31/31 全一致」這個綠燈的涵蓋範圍比它看起來的小。

此測試把「新表要嘛進檢查、要嘛明寫豁免並附理由」變成硬關卡，讓漂移在 CI 就被擋下，
而不是等到真的要救資料時才發現沒驗到。
"""

import re

from app.core.db import Base

# 匯入所有模組的 models 才能讓 Base.metadata 完整（沿 tests/conftest.py 的註冊方式）；
# 只 import Base 會得到殘缺的表清單，讓這個守衛形同虛設。
from app.main import create_app
from app.modules.backup.restore import _KEY_TABLES
from app.scripts.restore_drill import FEATURE_CHECKS

# 明確豁免：**不含營運資料**或由其他項目間接涵蓋者。新增豁免必須附理由。
_EXEMPT: dict[str, str] = {
    # 執行紀錄類：還原到新環境後本來就該重新累積，不是要救回的營運資料。
    "backup_runs": "備份自身的執行紀錄",
    "restore_runs": "還原自身的執行紀錄",
    # 短效/可重建：還原後重新產生即可正常運作。
    "kiosk_pairing_codes": "配對碼短效一次性，過期即失效",
    "kiosk_device_sessions": "裝置連線階段，重新連線即重建",
    # 由主體表涵蓋的附屬明細（主體對得上，明細就在同一份 dump 裡）。
    "cart_session_events": "購物車事件流，主體由 cart_sessions 涵蓋",
    "pos_terminals": "配對狀態由 terminal_kiosk_pairings 與 kiosk_devices 涵蓋",
    "purchase_order_lines": "採購明細，主體由 purchase_orders 與 goods_receipts 涵蓋",
    "linepay_refund_attempts": "LINE Pay 退款嘗試，結果由 linepay_transactions 涵蓋",
    "store_credit_suggestion_log": "購物金建議值的計算日誌，可由帳本重算",
    "premium_rate_history": "溢價率異動史，現值在 settings，已涵蓋",
    "agreement_versions": "切結書條款文本，隨程式碼版本重建",
    # 主檔類：資料量小、變動少，且損毀會立刻在上面的功能檢查中顯現（品項查不到）。
    "brands": "商品主檔附屬（品牌）",
    "categories": "商品主檔附屬（分類）",
    "category_pricing_rules": "分類定價規則",
    "product_models": "商品型號主檔",
}


create_app()  # 觸發全部 models 註冊（見上方說明）


def _tables_in_feature_checks() -> set[str]:
    """從 FEATURE_CHECKS 的 SQL 取出被查詢的表名。"""
    names: set[str] = set()
    for _label, sql in FEATURE_CHECKS:
        names.update(re.findall(r"\bFROM\s+([a-z_][a-z0-9_]*)", sql, flags=re.IGNORECASE))
    return names


def test_every_table_is_either_verified_or_explicitly_exempt() -> None:
    covered = set(_KEY_TABLES) | _tables_in_feature_checks() | set(_EXEMPT)
    missing = sorted(set(Base.metadata.tables) - covered)
    assert not missing, (
        "以下資料表既不在還原驗證的涵蓋範圍，也沒有明寫豁免：\n  "
        + "\n  ".join(missing)
        + "\n\n請擇一：①加進 app/scripts/restore_drill.py 的 FEATURE_CHECKS（或 restore.py 的 "
        "_KEY_TABLES）②若確實不含需要救回的營運資料，加進本檔 _EXEMPT 並寫明理由。\n"
        "不處理的話，還原演練的綠燈會比實際涵蓋範圍樂觀。"
    )


def test_exemptions_all_refer_to_real_tables() -> None:
    """豁免名單不得殘留已不存在的表——否則會悄悄掩蓋掉同名新表。"""
    stale = sorted(set(_EXEMPT) - set(Base.metadata.tables))
    assert not stale, f"豁免名單中的表已不存在，請清除：{stale}"


def test_key_tables_and_feature_checks_refer_to_real_tables() -> None:
    """清單裡的表名必須真的存在（打錯字會讓該項永遠 ERR 卻沒人發現）。"""
    real = set(Base.metadata.tables)
    assert not sorted(set(_KEY_TABLES) - real), "「_KEY_TABLES」有不存在的表名"
    assert not sorted(_tables_in_feature_checks() - real), "FEATURE_CHECKS 有不存在的表名"
