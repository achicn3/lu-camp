"""購物金（store credit）模型：不可變帳本＋帳戶快取（docs/16 §1、ADR-012）。

`store_credit_ledger` 為事實來源：INSERT only，應用層 repository 只提供新增，
DB trigger（見 `LEDGER_IMMUTABLE_DDL`，metadata 建表與 migration 共用同一定義）
直接拒絕 UPDATE/DELETE——雙保險。`store_credit_accounts` 為快取（餘額＋樂觀鎖
版本），同時是寫入序列化的鎖定錨點（SELECT … FOR UPDATE，沿 D-1 模式）；
餘額必須隨時可從帳本重算（I-3 對帳）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DDL,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import StoreCreditEntryType, StoreCreditSourceType


def _enum_col(enum_cls: type) -> Enum:
    # 存 VARCHAR + CHECK（無 PG ENUM 型別，downgrade 乾淨），與既有模組一致。
    return Enum(enum_cls, native_enum=False, length=30, create_constraint=True)


class StoreCreditLedger(Base):
    """購物金帳本分錄（append-only；無 updated_at——本表永不更新）。"""

    __tablename__ = "store_credit_ledger"
    __table_args__ = (
        # 冪等（I-5，沿 D-2）：同來源同類型只能有一筆；MANUAL 因 source_id NULL 不受限
        # （人工校正以 audit_log＋reason 留痕）。
        UniqueConstraint(
            "store_id",
            "source_type",
            "source_id",
            "entry_type",
            name="uq_store_credit_ledger_source",
        ),
        # 一列只能被沖正一次（adversarial review high）：不同 source 重複沖同一列
        # 會重複退/扣款。部分唯一索引（NULL 不受限）。
        Index(
            "uq_store_credit_ledger_reversal_of",
            "reversal_of_id",
            unique=True,
            postgresql_where=text("reversal_of_id IS NOT NULL"),
        ),
        # DB 層租戶配對（adversarial medium）：contact 必須屬於同一 store——
        # 服務層檢查之外的持久層保證，杜絕回填/直插造成跨店帳。
        ForeignKeyConstraint(
            ["contact_id", "store_id"],
            ["contacts.id", "contacts.store_id"],
            name="fk_store_credit_ledger_contact_store",
        ),
        # 帳本列必有帳戶列（adversarial 第六輪 high）：孤兒帳本（無帳戶）會讓
        # 對帳與總負債漏算。寫入路徑先 lock_account（必建列）再插帳本，順序相容。
        ForeignKeyConstraint(
            ["store_id", "contact_id"],
            ["store_credit_accounts.store_id", "store_credit_accounts.contact_id"],
            name="fk_store_credit_ledger_account",
        ),
        # 供下方租戶綁定自參考 FK 指向（id 為 PK，本約束恆成立）。
        UniqueConstraint("id", "store_id", "contact_id", name="uq_store_credit_ledger_id_tenant"),
        # 沖正必須指向**同店同帳戶**的原列（adversarial 第三輪 high）：
        # 直插跨店/跨帳戶沖正在 DB 層即被擋。
        ForeignKeyConstraint(
            ["reversal_of_id", "store_id", "contact_id"],
            [
                "store_credit_ledger.id",
                "store_credit_ledger.store_id",
                "store_credit_ledger.contact_id",
            ],
            name="fk_store_credit_ledger_reversal_tenant",
        ),
        # 人工校正（MANUAL，source_id NULL）改以冪等鍵防重複（adversarial 第三輪 high）。
        Index(
            "uq_store_credit_ledger_idem_key",
            "store_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        # 核心不變量收進 DB（adversarial 第四輪 medium）：繞過 service 的直插/回填
        # 也不能寫出不可能狀態（帳本不可變，寫錯無法修正）。
        # 【已知界線】Postgres 會在 CHECK 之前把輸入強制到 Numeric(12,0)（捨入），
        # 故「raw SQL 小數直插」會以捨入後的值通過 CHECK——此為全專案金額欄位
        # 的共同性質（§6 整數元慣例）。應用層由 _write_entry 整數守衛擋；
        # 直插造成的 sum/balance_after 不一致由 reconcile（I-3）偵測回報。
        CheckConstraint("signed_amount <> 0", name="ck_scl_signed_nonzero"),
        # 方向/形狀（adversarial 第五輪 medium）：CREDIT 必正、DEBIT 必負、
        # REVERSAL 必有對象（且唯有它有）、ADJUSTMENT 必 MANUAL 無 source_id、
        # 其餘類型必有 source_id。
        CheckConstraint("entry_type <> 'CREDIT' OR signed_amount > 0", name="ck_scl_credit_pos"),
        CheckConstraint("entry_type <> 'DEBIT' OR signed_amount < 0", name="ck_scl_debit_neg"),
        CheckConstraint(
            "(entry_type = 'REVERSAL') = (reversal_of_id IS NOT NULL)",
            name="ck_scl_reversal_ref",
        ),
        CheckConstraint(
            "entry_type <> 'ADJUSTMENT' OR (source_type = 'MANUAL' AND source_id IS NULL)",
            name="ck_scl_adjust_manual",
        ),
        CheckConstraint(
            "entry_type = 'ADJUSTMENT' OR source_id IS NOT NULL",
            name="ck_scl_source_required",
        ),
        # 來源-類型綁定（adversarial 第十二輪 medium）：算術自洽但掛錯業務事件的
        # 列，對帳抓不到——形狀收進 DB。
        CheckConstraint(
            "entry_type <> 'CREDIT' OR source_type = 'ACQUISITION'",
            name="ck_scl_credit_source",
        ),
        CheckConstraint(
            "entry_type <> 'DEBIT' OR source_type = 'SALE'",
            name="ck_scl_debit_source",
        ),
        CheckConstraint(
            "entry_type <> 'REVERSAL' OR source_type IN ('SALE_VOID', 'ACQUISITION_ROLLBACK')",
            name="ck_scl_reversal_source",
        ),
        CheckConstraint("balance_after >= 0", name="ck_scl_balance_after_nonneg"),
        CheckConstraint(
            "entry_type <> 'CREDIT' OR"
            " (cash_equivalent IS NOT NULL AND premium_rate_applied IS NOT NULL)",
            name="ck_scl_credit_fields",
        ),
        CheckConstraint(
            "entry_type <> 'ADJUSTMENT' OR (reason IS NOT NULL AND idempotency_key IS NOT NULL)",
            name="ck_scl_adjust_fields",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    contact_id: Mapped[int] = mapped_column(index=True)  # 複合 FK 見 __table_args__
    entry_type: Mapped[StoreCreditEntryType] = mapped_column(_enum_col(StoreCreditEntryType))
    signed_amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))  # 非零；方向見 enum
    balance_after: Mapped[Decimal] = mapped_column(Numeric(12, 0))  # 滾動餘額，恆 >= 0
    cash_equivalent: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))  # CREDIT 必填
    premium_rate_applied: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))  # CREDIT 必填
    source_type: Mapped[StoreCreditSourceType] = mapped_column(_enum_col(StoreCreditSourceType))
    source_id: Mapped[int | None] = mapped_column()
    reversal_of_id: Mapped[int | None] = mapped_column()  # 租戶綁定複合自參考 FK 見 __table_args__
    fingerprint: Mapped[str] = mapped_column(String(64))  # 內容 sha256（冪等比對）
    idempotency_key: Mapped[str | None] = mapped_column(String(80))  # MANUAL 校正用
    reason: Mapped[str | None] = mapped_column(String(200))  # ADJUSTMENT 必填
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # G3 前恆 NULL
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class StoreCreditAccount(Base, TimestampMixin):
    """帳戶快取（每店每 contact 一列）：餘額＋版本（樂觀鎖）；寫入鎖定錨點。"""

    __tablename__ = "store_credit_accounts"
    __table_args__ = (
        UniqueConstraint("store_id", "contact_id", name="uq_store_credit_accounts_contact"),
        CheckConstraint("balance >= 0", name="ck_sca_balance_nonneg"),
        ForeignKeyConstraint(
            ["contact_id", "store_id"],
            ["contacts.id", "contacts.store_id"],
            name="fk_store_credit_accounts_contact_store",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    contact_id: Mapped[int] = mapped_column(index=True)  # 複合 FK 見 __table_args__
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 0), default=Decimal(0))
    version: Mapped[int] = mapped_column(default=0, server_default=text("0"))


# 帳本不可變 trigger（I-1 雙保險）：metadata 建表（測試）與 migration 共用同一定義，
# 避免兩處漂移。任何 UPDATE/DELETE 直接 RAISE。
# 每條一個語句（asyncpg prepared statement 不接受多語句）。
LEDGER_IMMUTABLE_DDL: tuple[str, ...] = (
    """
CREATE OR REPLACE FUNCTION store_credit_ledger_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'store_credit_ledger 為 insert-only（ADR-012）：禁止 UPDATE/DELETE';
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE TRIGGER trg_store_credit_ledger_immutable
BEFORE UPDATE OR DELETE ON store_credit_ledger
FOR EACH ROW EXECUTE FUNCTION store_credit_ledger_immutable()
""",
    # 沖正跨列不變量（adversarial 第十輪 high）：CHECK 無法跨列，改以 BEFORE INSERT
    # trigger 守——沖正對象不可是沖正列、金額必為原列負值。帳本不可變＋一列僅一個
    # 沖正名額，寫錯即永久佔用名額並改變負債，必須在持久層擋。
    """
CREATE OR REPLACE FUNCTION store_credit_reversal_guard() RETURNS trigger AS $$
DECLARE
  original RECORD;
BEGIN
  IF NEW.reversal_of_id IS NULL THEN
    RETURN NEW;
  END IF;
  SELECT entry_type, signed_amount, source_type, source_id INTO original
    FROM store_credit_ledger WHERE id = NEW.reversal_of_id;
  IF original.entry_type = 'REVERSAL' THEN
    RAISE EXCEPTION '沖正列不可再被沖正';
  END IF;
  IF NEW.signed_amount <> -original.signed_amount THEN
    RAISE EXCEPTION '沖正金額必須為原列負值';
  END IF;
  IF NEW.source_type = 'SALE_VOID'
     AND (original.entry_type <> 'DEBIT' OR original.source_type <> 'SALE') THEN
    RAISE EXCEPTION 'SALE_VOID 只能沖 DEBIT/SALE 列';
  END IF;
  IF NEW.source_type = 'ACQUISITION_ROLLBACK'
     AND (original.entry_type <> 'CREDIT' OR original.source_type <> 'ACQUISITION') THEN
    RAISE EXCEPTION 'ACQUISITION_ROLLBACK 只能沖 CREDIT/ACQUISITION 列';
  END IF;
  IF NEW.source_id <> original.source_id THEN
    RAISE EXCEPTION '沖正 source_id 必須等於原列 source_id';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE TRIGGER trg_store_credit_reversal_guard
BEFORE INSERT ON store_credit_ledger
FOR EACH ROW EXECUTE FUNCTION store_credit_reversal_guard()
""",
    # CREDIT 經濟不變量（adversarial 第十一輪 high）：I-4 的三值關係收進 DB——
    # 回填/直插不可寫出「自洽加總但經濟錯誤」的 CREDIT（如等值 100、溢價 0.1、
    # 實發 999）。ROUND(numeric) 為 half-away-from-zero，正數時等同 §6 的
    # ROUND_HALF_UP（core/money.round_ntd）。溢價政策界線 0–0.2000 與 service
    # 常數一致；SC-5 若放寬政策須連動 migration（金錢級變更本應慎重留痕）。
    """
CREATE OR REPLACE FUNCTION store_credit_credit_guard() RETURNS trigger AS $$
BEGIN
  IF NEW.entry_type <> 'CREDIT' THEN
    RETURN NEW;
  END IF;
  IF NEW.cash_equivalent <= 0 THEN
    RAISE EXCEPTION 'CREDIT 現金等值必須為正';
  END IF;
  IF NEW.premium_rate_applied < 0 OR NEW.premium_rate_applied > 0.2000 THEN
    RAISE EXCEPTION 'CREDIT 溢價率超出政策界線';
  END IF;
  IF NEW.signed_amount <> ROUND(NEW.cash_equivalent * (1 + NEW.premium_rate_applied)) THEN
    RAISE EXCEPTION 'CREDIT 實發額必須等於 round(現金等值 × (1+溢價率))';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE TRIGGER trg_store_credit_credit_guard
BEFORE INSERT ON store_credit_ledger
FOR EACH ROW EXECUTE FUNCTION store_credit_credit_guard()
""",
    # 滾動餘額鏈（adversarial 第十五輪 high）：每列 balance_after 必等於
    # 既有和＋本列——直插/回填寫不出假歷史餘額（service 路徑持帳戶鎖、
    # 同帳戶序列化，計算一致；raw 並發直插最壞情況是被拒）。
    """
CREATE OR REPLACE FUNCTION store_credit_balance_chain_guard() RETURNS trigger AS $$
DECLARE
  prior NUMERIC;
BEGIN
  -- 先鎖帳戶列再算前和（第十六輪 high）：READ COMMITTED 下兩個並發直插
  -- 會讀到同一前和、雙雙通過——以帳戶列鎖在 DB 層序列化（service 路徑
  -- 本就持同一鎖，重入無害）。
  PERFORM 1 FROM store_credit_accounts
    WHERE store_id = NEW.store_id AND contact_id = NEW.contact_id
    FOR UPDATE;
  SELECT COALESCE(SUM(signed_amount), 0) INTO prior
    FROM store_credit_ledger
    WHERE store_id = NEW.store_id AND contact_id = NEW.contact_id;
  IF NEW.balance_after <> prior + NEW.signed_amount THEN
    RAISE EXCEPTION 'balance_after 必須等於滾動和（前和＋本列）';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE TRIGGER trg_store_credit_balance_chain_guard
BEFORE INSERT ON store_credit_ledger
FOR EACH ROW EXECUTE FUNCTION store_credit_balance_chain_guard()
""",
)

LEDGER_IMMUTABLE_DROP_DDL: tuple[str, ...] = (
    "DROP TRIGGER IF EXISTS trg_store_credit_balance_chain_guard ON store_credit_ledger",
    "DROP FUNCTION IF EXISTS store_credit_balance_chain_guard()",
    "DROP TRIGGER IF EXISTS trg_store_credit_credit_guard ON store_credit_ledger",
    "DROP FUNCTION IF EXISTS store_credit_credit_guard()",
    "DROP TRIGGER IF EXISTS trg_store_credit_reversal_guard ON store_credit_ledger",
    "DROP FUNCTION IF EXISTS store_credit_reversal_guard()",
    "DROP TRIGGER IF EXISTS trg_store_credit_ledger_immutable ON store_credit_ledger",
    "DROP FUNCTION IF EXISTS store_credit_ledger_immutable()",
)

for _ddl in LEDGER_IMMUTABLE_DDL:
    # sqlalchemy.DDL 無型別標註（第三方 stub 缺口），定向忽略、非弱化專案型別。
    event.listen(StoreCreditLedger.__table__, "after_create", DDL(_ddl))  # type: ignore[no-untyped-call]
