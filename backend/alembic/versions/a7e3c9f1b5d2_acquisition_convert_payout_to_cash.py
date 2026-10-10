"""收購購物金「改成付現」：放行購物金已整筆沖回後把收購的購物金腿歸零（店主 2026-10-10）。

客人選了購物金、送出後反悔要現金：當初的購物金整筆沖回、從抽屜付出溢價前的價值，
收購單撥款方式改成現金。原本的資料庫守衛（收購購物金腿 ↔ 帳本 ACQUISITION CREDIT 恆等）
一律擋下歸零；本版只為「購物金已整筆沖回（不是選品作廢）、收購改由等額現金腿承擔」放行，
其他情況照舊擋下（不能憑空消滅或鑄造購物金負債）。

- 新函式 acquisition_credit_converted_to_cash(acq_id)：判斷上述情況。
- acquisitions_credit_leg_guard、store_credit_ledger_acq_source_guard 兩邊守衛都認這個例外。

降版：已經有改成付現的收購時拒絕（舊守衛不認這種狀態，之後改那幾筆收購會失敗）。

Revision ID: a7e3c9f1b5d2
Revises: c8e2a6f0d4b7
"""

from alembic import op

from app.modules.acquisition.models import ACQ_CREDIT_LEG_GUARD_DDL, LEDGER_ACQ_SOURCE_GUARD_DDL

revision = "a7e3c9f1b5d2"
down_revision = "c8e2a6f0d4b7"
branch_labels = None
depends_on = None

# 新版：判斷函式＋兩邊守衛函式（只換函式本體，trigger 不動）。
_NEW_FUNCTIONS = (
    ACQ_CREDIT_LEG_GUARD_DDL[0],  # acquisition_credit_converted_to_cash
    ACQ_CREDIT_LEG_GUARD_DDL[2],  # acquisitions_credit_leg_guard
    LEDGER_ACQ_SOURCE_GUARD_DDL[0],  # store_credit_ledger_acq_source_guard
)

# 降版還原成 c8e2a6f0d4b7 時的守衛本體（逐字保留，不從 models 讀——models 之後會再變）。
_OLD_ACQ_CREDIT_LEG_GUARD = """
CREATE OR REPLACE FUNCTION acquisitions_credit_leg_guard() RETURNS trigger AS $$
DECLARE
  led_store INT;
  led_contact INT;
  led_ce NUMERIC;
BEGIN
  IF TG_OP = 'DELETE' THEN
    PERFORM 1 FROM store_credit_ledger
     WHERE source_type = 'ACQUISITION' AND entry_type = 'CREDIT' AND source_id = OLD.id;
    IF FOUND THEN
      RAISE EXCEPTION '收購已產生購物金分錄，不可刪除（會留下孤兒購物金負債）';
    END IF;
    RETURN OLD;
  END IF;
  -- 以分錄是否存在為準（不看 NEW.credit）：找到本收購對應的 CREDIT 分錄
  SELECT store_id, contact_id, cash_equivalent INTO led_store, led_contact, led_ce
    FROM store_credit_ledger
   WHERE source_type = 'ACQUISITION' AND entry_type = 'CREDIT' AND source_id = NEW.id;
  IF NOT FOUND THEN
    -- 無分錄：僅在收購本就無 credit 腿時合法（CASH／純付現）
    IF COALESCE(NEW.payout_credit_cash_equivalent, 0) <> 0 THEN
      RAISE EXCEPTION '收購購物金腿必須對應同店同對象等值的帳本 ACQUISITION CREDIT 分錄';
    END IF;
    RETURN NEW;
  END IF;
  -- 有分錄：收購身分必須恆等對應（擋歸零/改 store/改 contact/改金額）
  IF led_store <> NEW.store_id OR led_contact <> NEW.contact_id
     OR led_ce <> COALESCE(NEW.payout_credit_cash_equivalent, 0) THEN
    RAISE EXCEPTION '收購購物金腿必須對應同店同對象等值的帳本 ACQUISITION CREDIT 分錄';
  END IF;
  -- 庫存背書（空殼收購不可鑄造負債）
  PERFORM acquisitions_verify_credit_backing(NEW.id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_OLD_LEDGER_ACQ_SOURCE_GUARD = """
CREATE OR REPLACE FUNCTION store_credit_ledger_acq_source_guard() RETURNS trigger AS $$
DECLARE
  acq_credit NUMERIC;
BEGIN
  IF NEW.entry_type <> 'CREDIT' OR NEW.source_type <> 'ACQUISITION' THEN
    RETURN NEW;
  END IF;
  SELECT payout_credit_cash_equivalent INTO acq_credit
    FROM acquisitions
   WHERE id = NEW.source_id AND store_id = NEW.store_id
     AND contact_id = NEW.contact_id;
  IF acq_credit IS NULL OR acq_credit <> NEW.cash_equivalent THEN
    RAISE EXCEPTION 'ACQUISITION CREDIT 分錄必須對應同店同對象、credit 腿等值的收購';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_CONVERTED_EXISTS = """
SELECT 1 FROM acquisitions a
 WHERE COALESCE(a.payout_credit_cash_equivalent, 0) = 0
   AND EXISTS (
     SELECT 1 FROM store_credit_ledger c
      WHERE c.source_type = 'ACQUISITION' AND c.entry_type = 'CREDIT' AND c.source_id = a.id
   )
 LIMIT 1
"""


def upgrade() -> None:
    for ddl in _NEW_FUNCTIONS:
        op.execute(ddl)


def downgrade() -> None:
    if op.get_bind().exec_driver_sql(_CONVERTED_EXISTS).first() is not None:
        raise RuntimeError("已有改成付現的收購，舊版守衛不認這種狀態，拒絕降版")
    op.execute(_OLD_ACQ_CREDIT_LEG_GUARD)
    op.execute(_OLD_LEDGER_ACQ_SOURCE_GUARD)
    op.execute("DROP FUNCTION IF EXISTS acquisition_credit_converted_to_cash(BIGINT)")
