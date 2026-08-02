"""贈品與臨時折扣的資料模型（P1）

新增：
- 原因代碼表 `gift_reasons` / `discount_reasons`（停用不實刪，歷史單據另存名稱快照）
- `sale_lines`：`line_kind`（商業性質，與品項種類 `line_type` 正交）、
  `manual_discount_amount`、`net_amount`（本行實付）、`cost_snapshot`（成交當下成本）、
  贈品三欄與 `parent_sale_line_id`
- `catalog_products.unit_cost`（先前完全沒有成本欄位）
- `sale_adjustments` / `sale_adjustment_allocations`
- `StockReason` 加 `GIFT` / `GIFT_RETURN`（VARCHAR+CHECK → 重建約束）

**成本快照的回填口徑**：以「當前庫存成本」回填歷史明細——這正是報表今天在做的事
（即時 join 回庫存取成本），所以回填後歷史報表數字**完全不變**，只是把今天的行為凍結住，
日後調整商品成本才不會回頭改寫歷史毛利。一般商品先前無成本欄位，一律留 NULL，
沿用既有的「成本未知」口徑（報表不假造毛利）。

Revision ID: b5d7f9a1c3e2
Revises: a3c5e7f9b1d2
Create Date: 2026-08-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5d7f9a1c3e2"
down_revision: str | Sequence[str] | None = "a3c5e7f9b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STOCK_REASON_CK = "stockreason"
_OLD_REASONS = (
    "ACQUISITION",
    "PURCHASE",
    "SALE",
    "RETURN",
    "CONSIGN_RETURN",
    "WRITE_OFF",
    "STOCKTAKE",
)
_NEW_REASONS = (*_OLD_REASONS, "GIFT", "GIFT_RETURN")

_DEFAULT_GIFT_REASONS = (
    ("PROMOTION", "活動贈品", False),
    ("COMPLAINT", "客訴補償", True),
    ("LOYALTY", "熟客回饋", False),
    ("DISPLAY", "展示品或即期品", False),
    ("OTHER", "其他", True),
)
_DEFAULT_DISCOUNT_REASONS = (
    ("DEFECT", "商品瑕疵", True),
    ("COMPLAINT", "客訴補償", True),
    ("LOYALTY", "熟客優惠", False),
    ("DISPLAY", "即期或展示品", False),
    ("MANAGER", "店長授權", True),
    ("PROMOTION", "活動優惠", False),
    ("OTHER", "其他", True),
)


_TENDER_GUARD_NEW = """
CREATE OR REPLACE FUNCTION sales_verify_tender_total(p_sale_id BIGINT) RETURNS void AS $$
DECLARE
  sale_total NUMERIC;
  tender_sum NUMERIC;
BEGIN
  SELECT total INTO sale_total FROM sales WHERE id = p_sale_id;
  IF NOT FOUND THEN
    RETURN;
  END IF;
  IF sale_total < 0 THEN
    RAISE EXCEPTION '銷售總額不可為負';
  END IF;
  SELECT COALESCE(SUM(amount), 0) INTO tender_sum FROM sale_tenders WHERE sale_id = p_sale_id;
  IF sale_total = 0 THEN
    IF tender_sum <> 0 THEN
      RAISE EXCEPTION '零元銷售不得有收款明細';
    END IF;
    -- 必須「有明細，且全部是贈品」。少了「有明細」這一半，一張沒有任何明細的
    -- 零元單就會被放行（raw DML 建空單）。
    IF NOT EXISTS (SELECT 1 FROM sale_lines WHERE sale_id = p_sale_id)
       OR EXISTS (
            SELECT 1 FROM sale_lines
            WHERE sale_id = p_sale_id AND line_kind <> 'GIFT'
          ) THEN
      RAISE EXCEPTION '零元銷售必須整單都是贈品（一般商品折到 0 元請改開贈品）';
    END IF;
    RETURN;
  END IF;
  IF tender_sum <> sale_total THEN
    RAISE EXCEPTION '收款明細加總必須等於銷售總額（sale_tenders 與 sales.total 不對平）';
  END IF;
END;
$$ LANGUAGE plpgsql
"""

_TENDER_GUARD_OLD = """
CREATE OR REPLACE FUNCTION sales_verify_tender_total(p_sale_id BIGINT) RETURNS void AS $$
DECLARE
  sale_total NUMERIC;
  tender_sum NUMERIC;
BEGIN
  SELECT total INTO sale_total FROM sales WHERE id = p_sale_id;
  IF NOT FOUND THEN
    RETURN;
  END IF;
  IF sale_total <= 0 THEN
    RAISE EXCEPTION '銷售總額必須大於 0';
  END IF;
  SELECT COALESCE(SUM(amount), 0) INTO tender_sum FROM sale_tenders WHERE sale_id = p_sale_id;
  IF tender_sum <> sale_total THEN
    RAISE EXCEPTION '收款明細加總必須等於銷售總額（sale_tenders 與 sales.total 不對平）';
  END IF;
END;
$$ LANGUAGE plpgsql
"""


def _replace_stock_reason_check(values: tuple[str, ...]) -> None:
    op.drop_constraint(_STOCK_REASON_CK, "stock_movements", type_="check")
    allowed = ", ".join(f"'{v}'" for v in values)
    op.create_check_constraint(
        _STOCK_REASON_CK, "stock_movements", sa.text(f"reason IN ({allowed})")
    )


def _reason_table(name: str) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("requires_note", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("store_id", "code", name=f"uq_{name}_store_code"),
        sa.UniqueConstraint("id", "store_id", name=f"uq_{name}_id_store"),
    )
    op.create_index(f"ix_{name}_store_id", name, ["store_id"])


def upgrade() -> None:
    # ── M1 原因代碼表 ───────────────────────────────────────────────────────
    _reason_table("gift_reasons")
    _reason_table("discount_reasons")
    # 每間店都給一組預設原因，否則第一次要送贈品時選單是空的。
    for table, rows in (
        ("gift_reasons", _DEFAULT_GIFT_REASONS),
        ("discount_reasons", _DEFAULT_DISCOUNT_REASONS),
    ):
        for order, (code, name, requires_note) in enumerate(rows):
            op.execute(
                sa.text(
                    f"INSERT INTO {table}"
                    " (store_id, code, name, is_active, requires_note, sort_order,"
                    " created_at, updated_at)"
                    " SELECT id, :code, :name, true, :rn, :ord, now(), now() FROM stores"
                ).bindparams(code=code, name=name, rn=requires_note, ord=order)
            )

    # ── M1 庫存異動原因 ─────────────────────────────────────────────────────
    _replace_stock_reason_check(_NEW_REASONS)

    # ── M3 一般商品成本 ─────────────────────────────────────────────────────
    op.add_column("catalog_products", sa.Column("unit_cost", sa.Numeric(12, 0), nullable=True))

    # ── M2 sale_lines 新欄位（先可空/帶預設，回填後再收緊）────────────────────
    op.add_column(
        "sale_lines",
        sa.Column("line_kind", sa.String(length=30), nullable=False, server_default="NORMAL"),
    )
    op.add_column(
        "sale_lines",
        sa.Column("manual_discount_amount", sa.Numeric(12, 0), nullable=False,
                  server_default=sa.text("0")),
    )
    op.add_column("sale_lines", sa.Column("net_amount", sa.Numeric(12, 0), nullable=True))
    op.add_column("sale_lines", sa.Column("cost_snapshot", sa.Numeric(12, 0), nullable=True))
    op.add_column(
        "sale_lines",
        sa.Column("gift_reason_id", sa.Integer(), sa.ForeignKey("gift_reasons.id"), nullable=True),
    )
    op.add_column("sale_lines", sa.Column("gift_reason_name", sa.String(length=50), nullable=True))
    op.add_column("sale_lines", sa.Column("gift_note", sa.String(length=200), nullable=True))
    op.add_column(
        "sale_lines",
        sa.Column("parent_sale_line_id", sa.Integer(), sa.ForeignKey("sale_lines.id"),
                  nullable=True),
    )
    op.create_check_constraint("stocklinekind", "sale_lines", sa.text(
        "line_kind IN ('NORMAL', 'GIFT')"
    ))

    # 回填：既有明細都是一般銷售、且無臨時折扣 → 實付＝活動折後金額。
    op.execute(sa.text("UPDATE sale_lines SET net_amount = line_total WHERE net_amount IS NULL"))
    op.alter_column("sale_lines", "net_amount", existing_type=sa.Numeric(12, 0), nullable=False)

    # ── M4 成本快照回填（見檔頭：回填後報表數字不變）──────────────────────────
    op.execute(
        sa.text(
            "UPDATE sale_lines sl SET cost_snapshot = si.acquisition_cost"
            " FROM serialized_items si"
            " WHERE sl.serialized_item_id = si.id AND si.acquisition_cost IS NOT NULL"
        )
    )
    # 散裝：每件成本 = acquisition_cost ÷ total_qty，四捨五入到整數元後乘數量
    # （與 reports 既有的 round_ntd(cost * qty / total_qty) 同口徑）。
    op.execute(
        sa.text(
            "UPDATE sale_lines sl"
            " SET cost_snapshot = round(bl.acquisition_cost * sl.qty / bl.total_qty)"
            " FROM bulk_lots bl"
            " WHERE sl.bulk_lot_id = bl.id AND bl.total_qty > 0"
        )
    )
    # 一般商品先前無成本欄位 → 留 NULL（沿用「成本未知」口徑）；餐飲無成本模型 → 留 NULL。

    # ── sale_lines 的形狀守衛（回填完成後才建，避免舊資料卡住）────────────────
    op.create_check_constraint(
        "ck_sale_lines_gift_shape", "sale_lines",
        sa.text(
            "line_kind <> 'GIFT' OR ("
            " unit_price = 0 AND line_total = 0 AND net_amount = 0"
            " AND discount_amount = 0 AND manual_discount_amount = 0"
            " AND original_unit_price IS NOT NULL AND gift_reason_id IS NOT NULL)"
        ),
    )
    op.create_check_constraint(
        "ck_sale_lines_net_amount_consistent", "sale_lines",
        sa.text("line_kind <> 'NORMAL' OR net_amount = line_total - manual_discount_amount"),
    )
    op.create_check_constraint(
        "ck_sale_lines_amounts_nonneg", "sale_lines",
        sa.text("net_amount >= 0 AND manual_discount_amount >= 0"),
    )

    # ── M5 折扣紀錄與分攤 ───────────────────────────────────────────────────
    op.create_table(
        "sale_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("sale_id", sa.Integer(), nullable=False),
        sa.Column("sale_line_id", sa.Integer(), sa.ForeignKey("sale_lines.id"), nullable=True),
        sa.Column("scope", sa.String(length=30), nullable=False),
        sa.Column("adjustment_type", sa.String(length=30), nullable=False,
                  server_default="MANUAL_DISCOUNT"),
        sa.Column("calculation_method", sa.String(length=30), nullable=False),
        sa.Column("requested_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("applied_amount", sa.Numeric(12, 0), nullable=False),
        sa.Column("reason_id", sa.Integer(), sa.ForeignKey("discount_reasons.id"), nullable=True),
        sa.Column("reason_name", sa.String(length=50), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("void_reason", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("id", "store_id", name="uq_sale_adjustments_id_store"),
        sa.ForeignKeyConstraint(["sale_id", "store_id"], ["sales.id", "sales.store_id"],
                                name="fk_sale_adjustments_sale_store"),
        sa.CheckConstraint("scope IN ('ORDER', 'ITEM')", name="adjustmentscope"),
        sa.CheckConstraint("adjustment_type IN ('MANUAL_DISCOUNT')", name="adjustmenttype"),
        sa.CheckConstraint("calculation_method IN ('FIXED_AMOUNT', 'PERCENTAGE')",
                           name="calculationmethod"),
        sa.CheckConstraint(
            "(scope = 'ITEM' AND sale_line_id IS NOT NULL)"
            " OR (scope = 'ORDER' AND sale_line_id IS NULL)",
            name="ck_sale_adjustments_scope_shape",
        ),
        sa.CheckConstraint("applied_amount >= 0", name="ck_sale_adjustments_applied_nonneg"),
        sa.CheckConstraint(
            "(voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL)"
            " OR (voided_at IS NOT NULL AND voided_by IS NOT NULL AND void_reason IS NOT NULL)",
            name="ck_sale_adjustments_void_shape",
        ),
    )
    op.create_index("ix_sale_adjustments_store_id", "sale_adjustments", ["store_id"])
    op.create_index("ix_sale_adjustments_sale_id", "sale_adjustments", ["sale_id"])

    op.create_table(
        "sale_adjustment_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("adjustment_id", sa.Integer(), sa.ForeignKey("sale_adjustments.id"),
                  nullable=False),
        sa.Column("sale_line_id", sa.Integer(), sa.ForeignKey("sale_lines.id"), nullable=False),
        sa.Column("allocated_amount", sa.Numeric(12, 0), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("adjustment_id", "sale_line_id", name="uq_sale_adjustment_alloc_pair"),
        sa.CheckConstraint("allocated_amount >= 0", name="ck_sale_adjustment_alloc_nonneg"),
    )
    op.create_index("ix_sale_adjustment_allocations_store_id", "sale_adjustment_allocations",
                    ["store_id"])
    op.create_index("ix_sale_adjustment_allocations_adjustment_id", "sale_adjustment_allocations",
                    ["adjustment_id"])
    op.create_index("ix_sale_adjustment_allocations_sale_line_id", "sale_adjustment_allocations",
                    ["sale_line_id"])

    # ── M6 放寬零元守衛（零元＝整單贈品，且不得有收款明細）─────────────────────
    op.execute(sa.text(_TENDER_GUARD_NEW))


def downgrade() -> None:
    op.execute(sa.text(_TENDER_GUARD_OLD))
    op.drop_table("sale_adjustment_allocations")
    op.drop_table("sale_adjustments")
    for name in (
        "ck_sale_lines_amounts_nonneg",
        "ck_sale_lines_net_amount_consistent",
        "ck_sale_lines_gift_shape",
        "stocklinekind",
    ):
        op.drop_constraint(name, "sale_lines", type_="check")
    for column in (
        "parent_sale_line_id",
        "gift_note",
        "gift_reason_name",
        "gift_reason_id",
        "cost_snapshot",
        "net_amount",
        "manual_discount_amount",
        "line_kind",
    ):
        op.drop_column("sale_lines", column)
    op.drop_column("catalog_products", "unit_cost")
    _replace_stock_reason_check(_OLD_REASONS)
    op.drop_table("discount_reasons")
    op.drop_table("gift_reasons")
