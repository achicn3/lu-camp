"""acquisition 模型：收購/寄售入庫單（單頭）。

入庫明細落在 inventory 的 serialized_items / bulk_lots（其 acquisition_id 外鍵回此）；
付現則記在 cashdrawer 的 cash_movements。本表只記單頭與付現總額。
金額用 NUMERIC(scale 0) → Decimal（NT$ 整數元）。
"""

from decimal import Decimal

from sqlalchemy import (
    DDL,
    CheckConstraint,
    Enum,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.modules.storecredit.models import StoreCreditLedger
from app.shared.enums import AcquisitionType, PayoutMethod


class Acquisition(Base, TimestampMixin):
    """收購/寄售入庫單。created_at 即收購日期；id 即收購單號。"""

    __tablename__ = "acquisitions"
    __table_args__ = (
        UniqueConstraint("store_id", "idempotency_key", name="uq_acquisitions_store_idem_key"),
        # NULL 僅限 legacy 回填列；新寫入一律非空（service 守衛＋本 CHECK 防空字串）。
        CheckConstraint(
            "idempotency_key IS NULL OR length(idempotency_key) > 0",
            name="ck_acquisitions_idem_key_nonempty",
        ),
        CheckConstraint(
            "payout_cash_amount IS NULL OR payout_cash_amount >= 0",
            name="ck_acquisitions_payout_cash_nonneg",
        ),
        CheckConstraint(
            "payout_credit_cash_equivalent IS NULL OR payout_credit_cash_equivalent >= 0",
            name="ck_acquisitions_payout_credit_nonneg",
        ),
        # 形狀綁定（Codex 第十一輪 medium）：method/type/三金額欄互相一致，
        # 回填/直插也寫不出「報表無法解讀為現金流出 vs 購物金負債」的怪單。
        CheckConstraint(
            "type <> 'CONSIGNMENT' OR (payout_method = 'CASH'"
            " AND payout_cash_amount IS NULL AND payout_credit_cash_equivalent IS NULL"
            " AND total_cash_paid IS NULL)",
            name="ck_acquisitions_consignment_no_payout",
        ),
        CheckConstraint(
            "payout_method <> 'CASH' OR type = 'CONSIGNMENT'"
            " OR (payout_cash_amount IS NOT NULL AND total_cash_paid IS NOT NULL"
            " AND payout_cash_amount = total_cash_paid"
            " AND COALESCE(payout_credit_cash_equivalent, 0) = 0)",
            name="ck_acquisitions_cash_shape",
        ),
        CheckConstraint(
            "payout_method <> 'STORE_CREDIT' OR (payout_credit_cash_equivalent IS NOT NULL"
            " AND payout_credit_cash_equivalent > 0"
            " AND COALESCE(payout_cash_amount, 0) = 0 AND COALESCE(total_cash_paid, 0) = 0)",
            name="ck_acquisitions_store_credit_shape",
        ),
        CheckConstraint(
            "payout_method <> 'SPLIT' OR (payout_cash_amount IS NOT NULL"
            " AND payout_credit_cash_equivalent IS NOT NULL AND total_cash_paid IS NOT NULL"
            " AND payout_cash_amount > 0 AND payout_credit_cash_equivalent > 0"
            " AND total_cash_paid = payout_cash_amount)",
            name="ck_acquisitions_split_shape",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    type: Mapped[AcquisitionType] = mapped_column(
        Enum(AcquisitionType, native_enum=False, length=30, create_constraint=True)
    )
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    clerk_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # 付現總額（BUYOUT/BULK_LOT 用；CONSIGNMENT 不付現為 NULL）。
    total_cash_paid: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    # 撥款方式與拆分（SC-2，docs/16 §1.7）：現金部分走錢櫃、購物金部分入帳本；
    # 既有/CONSIGNMENT 資料為 CASH 預設。
    payout_method: Mapped[PayoutMethod] = mapped_column(
        Enum(PayoutMethod, native_enum=False, length=20, create_constraint=True),
        default=PayoutMethod.CASH,
        server_default=text("'CASH'"),
    )
    payout_cash_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    payout_credit_cash_equivalent: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    # 操作層冪等（D-2 模式；Codex SC-2 high）：重試不得重複入庫/付現/入購物金。
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    idempotency_fingerprint: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(500))


# credit 腿 ↔ 帳本綁定（Codex SC-2 第十六輪 medium、第十七/十八輪 P1）：收購頭與
# 其購物金分錄的身分（store/contact/credit 等值）必須恆等對應。DEFERRABLE INITIALLY
# DEFERRED：於 COMMIT 時驗（header 先插、分錄後插的正常時序不受影響）。守衛全程
# 以「分錄是否存在」為準（非看 NEW 的 credit 欄），故同時擋下：
#   - INSERT：有 credit 腿卻無等值分錄；
#   - UPDATE：把已產生分錄的收購改成 CASH／改 credit 為 0／改 store/contact
#     （第十八輪 P1：歸零/搬移會留下無主負債）；
#   - DELETE：刪掉已產生分錄的收購（第十八輪 P1：留下孤兒負債）。
# 分錄本身的經濟正確性由 SC-1 的帳本守衛鏈保證。
ACQ_CREDIT_LEG_GUARD_DDL: tuple[str, ...] = (
    """
CREATE OR REPLACE FUNCTION acquisitions_credit_leg_guard() RETURNS trigger AS $$
DECLARE
  led_store INT;
  led_contact INT;
  led_ce NUMERIC;
  body_sum NUMERIC;
  payout_total NUMERIC;
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
  -- 第十九輪 P2：產生購物金負債的收購必須有真實庫存實體，且實體成本加總等於
  -- 撥款總額（現金腿＋購物金腿）——否則為「空殼收購憑空鑄造負債」。
  -- 成本與撥款同源（BUYOUT＝Σ serialized_items、BULK_LOT＝Σ bulk_lots），皆整數元必相等。
  IF NEW.type = 'BULK_LOT' THEN
    SELECT COALESCE(SUM(acquisition_cost), 0) INTO body_sum
      FROM bulk_lots WHERE acquisition_id = NEW.id;
  ELSE
    SELECT COALESCE(SUM(acquisition_cost), 0) INTO body_sum
      FROM serialized_items WHERE acquisition_id = NEW.id;
  END IF;
  payout_total := COALESCE(NEW.payout_cash_amount, 0)
                + COALESCE(NEW.payout_credit_cash_equivalent, 0);
  IF body_sum <> payout_total THEN
    RAISE EXCEPTION '收購庫存實體成本加總必須等於撥款總額（空殼收購不可鑄造購物金負債）';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE CONSTRAINT TRIGGER trg_acquisitions_credit_leg_guard
AFTER INSERT OR UPDATE OR DELETE ON acquisitions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION acquisitions_credit_leg_guard()
""",
)

ACQ_CREDIT_LEG_GUARD_DROP_DDL: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS trg_acquisitions_credit_leg_guard ON acquisitions",
    "DROP FUNCTION IF EXISTS acquisitions_credit_leg_guard()",
)

# 反向綁定（Codex SC-2 第十七輪 P1）：帳本的 ACQUISITION CREDIT 分錄也必須對應
# 一筆同店同對象、credit 腿等值的收購頭——否則直插/直呼 storecredit service 可
# 憑空鑄造購物金負債（孤兒分錄）。同為 COMMIT 時驗：正常時序 header 先插、
# 分錄後插，同交易內彼此可見。
LEDGER_ACQ_SOURCE_GUARD_DDL: tuple[str, ...] = (
    """
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
""",
    """
CREATE CONSTRAINT TRIGGER trg_store_credit_ledger_acq_source_guard
AFTER INSERT OR UPDATE ON store_credit_ledger
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION store_credit_ledger_acq_source_guard()
""",
)

LEDGER_ACQ_SOURCE_GUARD_DROP_DDL: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS trg_store_credit_ledger_acq_source_guard ON store_credit_ledger",
    "DROP FUNCTION IF EXISTS store_credit_ledger_acq_source_guard()",
)

for _ddl in ACQ_CREDIT_LEG_GUARD_DDL:
    # 掛本表 after_create（只在表真正建立時觸發一次；metadata 層在 checkfirst
    # 重跑時會重複執行而撞 DuplicateObject）。plpgsql 函式主體對
    # store_credit_ledger 的引用於執行期才解析，故不需等 ledger 表先建。
    event.listen(Acquisition.__table__, "after_create", DDL(_ddl))  # type: ignore[no-untyped-call]

for _ddl in LEDGER_ACQ_SOURCE_GUARD_DDL:
    # 掛 ledger 表 after_create：trigger 本體只需 ledger 存在；plpgsql 函式對
    # acquisitions 的引用同樣於執行期才解析，不受兩表建立順序影響。
    event.listen(StoreCreditLedger.__table__, "after_create", DDL(_ddl))  # type: ignore[no-untyped-call]
