"""購物金作廢守衛改判 sales.status（2026-08 金流稽核 P0-1／P0-2）

Revision ID: f9d3b7a1c5e8
Revises: f2a7c4e19b83
Create Date: 2026-08-22 22:10:00.000000

`f4c5d6e7a8b9`（2026-08-01）把「這筆銷售是否作廢」從 `sales.invoice_status='VOID'`
搬到 `sales.status='VOIDED'`，並改了 21 處應用層查詢，但**兩支資料庫 guard 函式沒有跟著改**：

- `sales_ledger_sale_debit_guard`：SALE_VOID 沖正必須對應「已作廢的銷售」。
  判 `invoice_status` 的話，電子發票關閉時該筆交易根本沒有發票（恆 NOT_ISSUED），
  於是**每一次合法的「作廢購物金銷售」都會在 COMMIT 被擋**。
- `sales_verify_store_credit_consistency`：作廢且有購物金扣抵 ⟹ 必須有 SALE_VOID 沖正。
  判 `invoice_status` 的話，同月整筆退貨作廢原發票（ADR-014）後的 F0501 回寫
  （`invoice_status='VOID'`）會被誤判成「銷售已作廢」而擋下——但退貨回補購物金走的是
  REFUND/SALE_RETURN，本來就沒有 SALE_VOID 沖正。連平台回執事件都會一起回滾。

兩者都是 fail-closed（拒絕交易、不寫壞資料），且線上資料至今 0 筆命中，故無資料回填需求。

**本檔的 SQL 刻意內嵌、不 import `models.py` 的常數**：建表 migration 以 import 常數的方式
安裝 trigger，會讓「migration 執行的內容」隨模組日後的修改而變動（正是本次漏改沒有任何
migration 補上的原因）。migration 應是不可變的歷史快照。

只 CREATE OR REPLACE 函式本體，不動 trigger——trigger 以函式名綁定，替換函式即生效。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f9d3b7a1c5e8"
down_revision: str | Sequence[str] | None = "f2a7c4e19b83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONSISTENCY_FN_NEW = """
CREATE OR REPLACE FUNCTION sales_verify_store_credit_consistency(p_sale_id BIGINT)
RETURNS void AS $$
DECLARE
  sale_store INT;
  sale_buyer INT;
  sale_status TEXT;
  sc_tender NUMERIC;
  debit_abs NUMERIC;
  debit_contact INT;
BEGIN
  SELECT store_id, buyer_contact_id, status
    INTO sale_store, sale_buyer, sale_status
    FROM sales WHERE id = p_sale_id;
  IF NOT FOUND THEN
    RETURN;  -- sale 已不存在（如刪除）：交由 FK／帳本側守衛處理
  END IF;
  SELECT amount INTO sc_tender
    FROM sale_tenders WHERE sale_id = p_sale_id AND tender_type = 'STORE_CREDIT';
  sc_tender := COALESCE(sc_tender, 0);
  SELECT -signed_amount, contact_id INTO debit_abs, debit_contact
    FROM store_credit_ledger
   WHERE store_id = sale_store AND source_type = 'SALE' AND entry_type = 'DEBIT'
     AND source_id = p_sale_id;
  debit_abs := COALESCE(debit_abs, 0);
  IF sc_tender <> debit_abs THEN
    RAISE EXCEPTION '購物金收款必須對應等額的帳本 SALE 扣抵（sale_tenders 與 ledger 不一致）';
  END IF;
  IF sc_tender > 0 AND debit_contact IS DISTINCT FROM sale_buyer THEN
    RAISE EXCEPTION '購物金扣抵對象必須為該銷售的買方';
  END IF;
  -- 已作廢且有購物金扣抵 → 必須有對應沖正（第三輪 P2：raw UPDATE 設作廢不可漏沖回）。
  -- **判 sales.status，不判 invoice_status**（ADR-013／2026-08 金流稽核 P0-2）：後者是
  -- 發票的狀態。同月整筆退貨會作廢原發票（ADR-014）並把 invoice_status 寫成 VOID，但退貨
  -- 回補購物金走的是 REFUND/SALE_RETURN、本就沒有 SALE_VOID 沖正——用 invoice_status 判會
  -- 把那次回寫整筆擋掉，連平台回執事件都一起回滾。
  IF sc_tender > 0 AND sale_status = 'VOIDED' THEN
    PERFORM 1 FROM store_credit_ledger
     WHERE store_id = sale_store AND source_type = 'SALE_VOID' AND entry_type = 'REVERSAL'
       AND source_id = p_sale_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION '已作廢的購物金銷售必須有對應的沖正分錄（SALE_VOID）';
    END IF;
  END IF;
END;
$$ LANGUAGE plpgsql
"""

_DEBIT_GUARD_FN_NEW = """
CREATE OR REPLACE FUNCTION sales_ledger_sale_debit_guard() RETURNS trigger AS $$
DECLARE
  sale_buyer INT;
  sale_status TEXT;
  sc_tender NUMERIC;
BEGIN
  -- SALE_VOID 沖正（第四輪 P1）：只能對應「已作廢」的同店銷售——擋 raw 在銷售仍生效時
  -- 沖回購物金（憑空回補餘額）。與收款側「VOID 必有沖正」合為雙向不變量。
  IF NEW.entry_type = 'REVERSAL' AND NEW.source_type = 'SALE_VOID' THEN
    -- **判 sales.status，不判 invoice_status**（ADR-013／2026-08 金流稽核 P0-1）：
    -- 電子發票關閉時該筆交易根本沒有發票，invoice_status 恆為 NOT_ISSUED——用它判會讓
    -- 每一次合法的「作廢購物金銷售」都在 COMMIT 被擋，店員只能改用人工調整去湊。
    SELECT status INTO sale_status
      FROM sales WHERE id = NEW.source_id AND store_id = NEW.store_id;
    IF NOT FOUND OR sale_status <> 'VOIDED' THEN
      RAISE EXCEPTION 'SALE_VOID 沖正只能對應已作廢的同店銷售';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.entry_type <> 'DEBIT' OR NEW.source_type <> 'SALE' THEN
    RETURN NEW;
  END IF;
  -- 必對應「與本扣抵同店」的銷售（NEW.store_id），擋孤兒扣抵與跨店借殼 source_id
  SELECT buyer_contact_id INTO sale_buyer
    FROM sales WHERE id = NEW.source_id AND store_id = NEW.store_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'SALE 扣抵必須對應同店的銷售（孤兒或跨店扣抵）';
  END IF;
  IF NEW.contact_id IS DISTINCT FROM sale_buyer THEN
    RAISE EXCEPTION 'SALE 扣抵對象必須為該銷售的買方';
  END IF;
  SELECT amount INTO sc_tender
    FROM sale_tenders WHERE sale_id = NEW.source_id AND tender_type = 'STORE_CREDIT';
  IF COALESCE(sc_tender, 0) <> -NEW.signed_amount THEN
    RAISE EXCEPTION 'SALE 扣抵金額必須等於該銷售的購物金收款';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

# downgrade 用：`f4c5d6e7a8b9` 之前的**歷史原文**（判 invoice_status），
# 逐字取自本次修正前的 models.py，不由新版推導——推導出來的版本會帶著新版的註解，
# 說著與實際判定相反的話。
_CONSISTENCY_FN_OLD = """
CREATE OR REPLACE FUNCTION sales_verify_store_credit_consistency(p_sale_id BIGINT)
RETURNS void AS $$
DECLARE
  sale_store INT;
  sale_buyer INT;
  sale_status TEXT;
  sc_tender NUMERIC;
  debit_abs NUMERIC;
  debit_contact INT;
BEGIN
  SELECT store_id, buyer_contact_id, invoice_status
    INTO sale_store, sale_buyer, sale_status
    FROM sales WHERE id = p_sale_id;
  IF NOT FOUND THEN
    RETURN;  -- sale 已不存在（如刪除）：交由 FK／帳本側守衛處理
  END IF;
  SELECT amount INTO sc_tender
    FROM sale_tenders WHERE sale_id = p_sale_id AND tender_type = 'STORE_CREDIT';
  sc_tender := COALESCE(sc_tender, 0);
  SELECT -signed_amount, contact_id INTO debit_abs, debit_contact
    FROM store_credit_ledger
   WHERE store_id = sale_store AND source_type = 'SALE' AND entry_type = 'DEBIT'
     AND source_id = p_sale_id;
  debit_abs := COALESCE(debit_abs, 0);
  IF sc_tender <> debit_abs THEN
    RAISE EXCEPTION '購物金收款必須對應等額的帳本 SALE 扣抵（sale_tenders 與 ledger 不一致）';
  END IF;
  IF sc_tender > 0 AND debit_contact IS DISTINCT FROM sale_buyer THEN
    RAISE EXCEPTION '購物金扣抵對象必須為該銷售的買方';
  END IF;
  -- 已作廢且有購物金扣抵 → 必須有對應沖正（第三輪 P2：raw UPDATE 設 VOID 不可漏沖回）
  IF sc_tender > 0 AND sale_status = 'VOID' THEN
    PERFORM 1 FROM store_credit_ledger
     WHERE store_id = sale_store AND source_type = 'SALE_VOID' AND entry_type = 'REVERSAL'
       AND source_id = p_sale_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION '已作廢的購物金銷售必須有對應的沖正分錄（SALE_VOID）';
    END IF;
  END IF;
END;
$$ LANGUAGE plpgsql
"""

_DEBIT_GUARD_FN_OLD = """
CREATE OR REPLACE FUNCTION sales_ledger_sale_debit_guard() RETURNS trigger AS $$
DECLARE
  sale_buyer INT;
  sale_status TEXT;
  sc_tender NUMERIC;
BEGIN
  -- SALE_VOID 沖正（第四輪 P1）：只能對應「已作廢」的同店銷售——擋 raw 在銷售仍生效時
  -- 沖回購物金（憑空回補餘額）。與收款側「VOID 必有沖正」合為雙向不變量。
  IF NEW.entry_type = 'REVERSAL' AND NEW.source_type = 'SALE_VOID' THEN
    SELECT invoice_status INTO sale_status
      FROM sales WHERE id = NEW.source_id AND store_id = NEW.store_id;
    IF NOT FOUND OR sale_status <> 'VOID' THEN
      RAISE EXCEPTION 'SALE_VOID 沖正只能對應已作廢的同店銷售';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.entry_type <> 'DEBIT' OR NEW.source_type <> 'SALE' THEN
    RETURN NEW;
  END IF;
  -- 必對應「與本扣抵同店」的銷售（NEW.store_id），擋孤兒扣抵與跨店借殼 source_id
  SELECT buyer_contact_id INTO sale_buyer
    FROM sales WHERE id = NEW.source_id AND store_id = NEW.store_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'SALE 扣抵必須對應同店的銷售（孤兒或跨店扣抵）';
  END IF;
  IF NEW.contact_id IS DISTINCT FROM sale_buyer THEN
    RAISE EXCEPTION 'SALE 扣抵對象必須為該銷售的買方';
  END IF;
  SELECT amount INTO sc_tender
    FROM sale_tenders WHERE sale_id = NEW.source_id AND tender_type = 'STORE_CREDIT';
  IF COALESCE(sc_tender, 0) <> -NEW.signed_amount THEN
    RAISE EXCEPTION 'SALE 扣抵金額必須等於該銷售的購物金收款';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(_CONSISTENCY_FN_NEW)
    op.execute(_DEBIT_GUARD_FN_NEW)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(_CONSISTENCY_FN_OLD)
    op.execute(_DEBIT_GUARD_FN_OLD)
