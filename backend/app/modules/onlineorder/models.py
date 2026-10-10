"""線上點餐（店內端）模型（docs/44 §5.3）：發佈紀錄、桌位碼、已推到雲端的媒體；
O4 起加線上訂單、份數保留、回報雲端的重試佇列、雲端連線狀態。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import BrewCardArt, BrewCardTheme, BrewDrawEffect, MenuUpsellRole


def _choice(enum_cls: type) -> Enum:
    """存小寫值（客人頁直接用），CHECK 約束擋掉不認得的值。"""
    return Enum(
        enum_cls,
        native_enum=False,
        length=20,
        create_constraint=True,
        values_callable=lambda e: [m.value for m in e],
    )


class OnlineMenuPresentation(Base, TimestampMixin):
    """Customer-facing copy and display settings for an existing menu item."""

    __tablename__ = "online_menu_presentations"
    __table_args__ = (
        UniqueConstraint("store_id", "menu_item_id", name="uq_online_menu_presentations_item"),
        CheckConstraint(
            "low_stock_threshold BETWEEN 0 AND 9999",
            name="ck_online_menu_presentations_threshold",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_items.id", ondelete="CASCADE"))
    flavor_description: Mapped[str | None] = mapped_column(String(120))
    audience_description: Mapped[str | None] = mapped_column(String(120))
    is_new: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    limited_on: Mapped[date | None] = mapped_column(Date)
    show_remaining: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    low_stock_threshold: Mapped[int] = mapped_column(default=5, server_default=text("5"))
    hide_sold_out: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    # 加購角色（docs/63 §6）；沒設定＝不參與加購推薦。
    role: Mapped[MenuUpsellRole | None] = mapped_column(_choice(MenuUpsellRole), nullable=True)


class OnlineMenuExperience(Base, TimestampMixin):
    """手沖體驗卡（docs/63 §4「手沖體驗」）：既有品項＋預選選項的另一種呈現。

    **不存價格、成本、庫存**——售價由原品項加預選選項算，成交仍記原品項與選項 ID。
    原品項真刪時連帶刪除（cascade）；封存或預選選項失效時發佈會略過這張卡。
    """

    __tablename__ = "online_menu_experiences"
    __table_args__ = (
        CheckConstraint("sort_order BETWEEN 0 AND 9999", name="ck_online_menu_experiences_sort"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    menu_item_id: Mapped[int] = mapped_column(
        ForeignKey("menu_items.id", ondelete="CASCADE"), index=True
    )
    option_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)
    title: Mapped[str] = mapped_column(String(30))
    tag: Mapped[str | None] = mapped_column(String(12))
    origin: Mapped[str | None] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(String(300))
    includes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    theme: Mapped[BrewCardTheme] = mapped_column(_choice(BrewCardTheme))
    art: Mapped[BrewCardArt] = mapped_column(_choice(BrewCardArt))
    effect: Mapped[BrewDrawEffect] = mapped_column(_choice(BrewDrawEffect))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))


class OnlineMenuQuiz(Base, TimestampMixin):
    """「不知道喝什麼」引導推薦（docs/63 §2 M2a）：每店一份，題目與答案存成一份文件。

    `questions`：[{prompt, options: [{label, items: [{kind: item|experience, id}]}]}]——
    每個答案勾「適合的品項」，客人答完照被勾到的次數排序推薦。只引用既有品項／體驗卡，
    不存價格或庫存；發佈時只帶上架中的。
    """

    __tablename__ = "online_menu_quizzes"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), unique=True)
    is_active: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    questions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)


class OnlineMenuPopularity(Base, TimestampMixin):
    """人氣標籤設定（docs/63 §7 M2b）：每店一列；沒有列＝預設（開、30 天、10 份）。

    人氣只用 POS 真實成交算（餐飲品項淨銷量），不收集客人瀏覽或點擊。
    """

    __tablename__ = "online_menu_popularity"
    __table_args__ = (
        CheckConstraint(
            "window_days IN (7, 14, 30, 60, 90)", name="ck_online_menu_popularity_window"
        ),
        CheckConstraint("min_qty BETWEEN 1 AND 999", name="ck_online_menu_popularity_min_qty"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), unique=True)
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    window_days: Mapped[int] = mapped_column(default=30, server_default=text("30"))
    min_qty: Mapped[int] = mapped_column(default=10, server_default=text("10"))


class OnlineStaffPicks(Base, TimestampMixin):
    """店員推薦（店主 2026-10-10）：每店一份有序清單，引用餐飲品項、手沖體驗卡或帶著走商品。

    `items`：[{kind: item|experience|retail, id}]（retail 的 id 是一般商品 id）。
    只引用、不存價格或庫存；
    發佈時只帶上架中的。取代以前每個品項各自勾的「露坑推薦」。
    """

    __tablename__ = "online_staff_picks"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), unique=True)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)


class OnlineRetailListing(Base, TimestampMixin):
    """線上「帶回家」的零售商品（docs/63 §13、M1d）：引用既有一般商品的呈現設定。

    **不存價格、成本、庫存**——售價是商品含稅 `unit_price`、庫存是 `quantity_on_hand`。
    商品真刪時連帶刪除；停售時發佈會標售完。照片沿用菜單照片表（同一家店）。
    """

    __tablename__ = "online_retail_listings"
    __table_args__ = (
        UniqueConstraint(
            "store_id", "catalog_product_id", name="uq_online_retail_listings_product"
        ),
        CheckConstraint("sort_order BETWEEN 0 AND 9999", name="ck_online_retail_listings_sort"),
        ForeignKeyConstraint(
            ["store_id", "photo_sha256"],
            ["menu_photos.store_id", "menu_photos.sha256"],
            name="fk_online_retail_listings_photo",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    catalog_product_id: Mapped[int] = mapped_column(
        ForeignKey("catalog_products.id", ondelete="CASCADE"), index=True
    )
    description: Mapped[str | None] = mapped_column(String(300))
    # 加購角色（只用咖啡豆／濾掛）；沒設定＝不參與加購推薦。
    role: Mapped[MenuUpsellRole | None] = mapped_column(_choice(MenuUpsellRole), nullable=True)
    photo_sha256: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    sort_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))


class OnlineMenuPublication(Base, TimestampMixin):
    """每次「發佈到線上點餐」一筆。版本號用發佈時間（毫秒），本機與雲端失聯回滾後也只會往前。"""

    __tablename__ = "online_menu_publications"
    __table_args__ = (
        UniqueConstraint("store_id", "version", name="uq_online_menu_publications_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    version: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    item_count: Mapped[int] = mapped_column()
    published_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OnlineMenuAvailability(Base, TimestampMixin):
    """One durable current availability payload and monotonically increasing revision per store."""

    __tablename__ = "online_menu_availability"
    __table_args__ = (
        CheckConstraint("revision >= 0", name="ck_online_menu_availability_revision"),
        CheckConstraint(
            "delivery_state IN ('PENDING', 'DELIVERED', 'CONFLICT')",
            name="ck_online_menu_availability_delivery",
        ),
    )

    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), primary_key=True)
    menu_version: Mapped[int] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    delivery_state: Mapped[str] = mapped_column(String(10))
    last_error: Mapped[str | None] = mapped_column(String(200))


class OnlineTableCode(Base, TimestampMixin):
    """桌位碼（docs/44 §4.1）：QR 網址 `/t/<code>`。重發＝舊碼 retired、產生新碼。"""

    __tablename__ = "online_table_codes"
    __table_args__ = (
        Index(
            "uq_online_table_codes_active_label",
            "store_id",
            "label",
            unique=True,
            postgresql_where=text("retired_at IS NULL"),
        ),
        UniqueConstraint("code", name="uq_online_table_codes_code"),
        CheckConstraint(
            "service_mode IN ('DINE_IN', 'TAKEOUT')", name="ck_online_table_codes_mode"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    label: Mapped[str] = mapped_column(String(20))
    service_mode: Mapped[str] = mapped_column(String(10))
    code: Mapped[str] = mapped_column(String(64))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OnlinePushedMedia(Base, TimestampMixin):
    """已推到雲端的照片／字型（內容雜湊）。推過就不再推；雲端本來就冪等，這只是省流量。"""

    __tablename__ = "online_pushed_media"
    __table_args__ = (
        UniqueConstraint("store_id", "kind", "sha256", name="uq_online_pushed_media"),
        CheckConstraint("kind IN ('PHOTO', 'FONT')", name="ck_online_pushed_media_kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    sha256: Mapped[str] = mapped_column(String(64))


class OnlineOrder(Base, TimestampMixin):
    """從雲端拉到的線上訂單（docs/44 §4.3、§4.6）。`remote_id` 唯一＝重拉不會匯入兩次（C3）。

    `lines` 是客人送單時雲端驗過價的明細原樣（品項、選項、單價、數量、是否限量）；帶入結帳時
    以 POS 目前的菜單重新計價，差額由店員確認。`sale_id` 唯一＝一張線上單只會成立一筆銷售。
    """

    __tablename__ = "online_orders"
    __table_args__ = (
        UniqueConstraint("store_id", "remote_id", name="uq_online_orders_remote"),
        UniqueConstraint("sale_id", name="uq_online_orders_sale"),
        CheckConstraint("service_mode IN ('DINE_IN', 'TAKEOUT')", name="ck_online_orders_mode"),
        CheckConstraint(
            "sync_status IN ('IMPORTED', 'SETTLED', 'VOIDED')", name="ck_online_orders_sync"
        ),
        CheckConstraint(
            "hold_status IN ('NONE', 'HELD', 'REJECTED')", name="ck_online_orders_hold"
        ),
        CheckConstraint(
            "payment_status IN ('UNPAID', 'PAID', 'CANCELLED')", name="ck_online_orders_payment"
        ),
        # 成立銷售＝已付款且有銷售單；其他狀態不能掛銷售單。
        CheckConstraint(
            "(sync_status = 'SETTLED') = (sale_id IS NOT NULL AND payment_status = 'PAID')",
            name="ck_online_orders_settled_sale",
        ),
        CheckConstraint("total >= 0", name="ck_online_orders_total_nonneg"),
        CheckConstraint(
            "fulfillment_status IN ('NONE', 'AWAITING', 'HANDED_OVER')",
            name="ck_online_orders_fulfillment",
        ),
        # 交貨只在成立銷售之後；交了貨一定有時間與經手人。
        CheckConstraint(
            "fulfillment_status <> 'HANDED_OVER' OR "
            "(sync_status = 'SETTLED' AND handed_over_at IS NOT NULL "
            "AND handed_over_by IS NOT NULL)",
            name="ck_online_orders_handed_over",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    remote_id: Mapped[str] = mapped_column(String(32))
    table_label: Mapped[str | None] = mapped_column(String(20))
    service_mode: Mapped[str] = mapped_column(String(10))
    menu_version: Mapped[int] = mapped_column(BigInteger)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    payment_method: Mapped[str] = mapped_column(String(10))
    note: Mapped[str | None] = mapped_column(String(200))
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    remote_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sync_status: Mapped[str] = mapped_column(String(10))
    hold_status: Mapped[str] = mapped_column(String(10))
    payment_status: Mapped[str] = mapped_column(String(10))
    # 庫存不夠時給店員看的原因（哪一項不夠）；客人畫面只看到「已售完」。
    reject_reason: Mapped[str | None] = mapped_column(String(300))
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # 帶回家商品的交貨（docs/63 §13）：付款與交貨分開，店員交給客人時按「已交貨」才結單。
    fulfillment_status: Mapped[str] = mapped_column(
        String(12), default="NONE", server_default=text("'NONE'")
    )
    handed_over_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 要交的商品＝實際結帳的一般商品與數量（[{catalog_product_id, name, qty}]），不是客人原本點的。
    handover_items: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # 線上 LINE Pay（docs/44 §4.4.2）：雲端回報客人已付款才有；POS 據此成立銷售、不再扣款。
    linepay_order_id: Mapped[str | None] = mapped_column(String(64))
    linepay_transaction_id: Mapped[str | None] = mapped_column(String(32))
    invoice_carrier: Mapped[str | None] = mapped_column(String(8))
    invoice_tax_id: Mapped[str | None] = mapped_column(String(8))
    # POS 沒辦法自動成立銷售的原因（例如價格與客人付的不同），給店員看。
    attention: Mapped[str | None] = mapped_column(String(300))
    handed_over_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class StockReservation(Base, TimestampMixin):
    """線上單保留的每日限量份數（docs/44 §3.7 O4 定案）：拉單時已直接扣掉，這裡記扣了什麼。

    `consumed`＝[{qty, consumed: [{kind, id, generation, day}]}]（每行一筆，格式同銷售明細的
    `menu_stock_consumed`），加回時照份數版本核對。一張線上單最多一筆。
    """

    __tablename__ = "stock_reservations"
    __table_args__ = (
        UniqueConstraint("online_order_id", name="uq_stock_reservations_order"),
        CheckConstraint(
            "status IN ('ACTIVE', 'CONVERTED', 'RELEASED', 'EXPIRED')",
            name="ck_stock_reservations_status",
        ),
        CheckConstraint(
            "(status = 'ACTIVE') = (ended_at IS NULL)", name="ck_stock_reservations_ended"
        ),
        Index(
            "ix_stock_reservations_active",
            "store_id",
            "expires_at",
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    online_order_id: Mapped[int] = mapped_column(ForeignKey("online_orders.id"))
    consumed: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(10))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OnlineOrderOutbox(Base, TimestampMixin):
    """回報雲端的持久化重試佇列（docs/44 §4.5 C4）：本機先 commit，回報排進來慢慢送。

    同一張單照 id 先後送（先 IMPORTED 才 SETTLED）。雲端明確拒收（狀態不合法、找不到單）
    標 DEAD，不無限重試；連不上或樂觀鎖衝突則退避重試。
    """

    __tablename__ = "online_order_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'SENT', 'DEAD')", name="ck_online_order_outbox_status"
        ),
        Index(
            "ix_online_order_outbox_pending",
            "store_id",
            "id",
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    online_order_id: Mapped[int] = mapped_column(ForeignKey("online_orders.id"), index=True)
    remote_id: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(10))
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(200))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OnlineOrderLink(Base, TimestampMixin):
    """和雲端的連線狀態（每店一列）：最後一次拉單、雲端說的接單／暫停與原因。給 POS 顯示用。"""

    __tablename__ = "online_order_links"

    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), primary_key=True)
    accepting: Mapped[bool | None] = mapped_column(Boolean)
    paused_reason: Mapped[str | None] = mapped_column(String(100))
    last_pull_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_pull_error: Mapped[str | None] = mapped_column(String(200))
