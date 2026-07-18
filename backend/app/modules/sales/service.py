"""sales 業務邏輯：POS 結帳的單交易 orchestrate。

於單一 DB 交易內協調 inventory / cashdrawer / consignment / settings 四個 service：
扣庫存（用 T5 原子機制，不先查再改）、寫 stock_movement(OUT)、收現 cash_movement(SALE_IN)、
總額層級推稅一次、寄售品建 PENDING 結算。任一步失敗整筆回復（本層只 flush、不 commit；
commit/rollback 由呼叫端控制），不留「庫存扣了但現金沒進」之類的半套。跨模組一律經對方 service。

守門：
- 收現必須在開帳中的 cash_session 下（§7.8），無開帳 → 整筆擋（最先檢查，未動任何庫存）。
- 序號品以原子轉移 IN_STOCK→SOLD 為售出保證（已 SOLD 不可再賣、併發只成功一筆）。
- 散裝 remaining_qty / 數量品 quantity_on_hand 以條件式 UPDATE 原子扣減，不得 < 0。
金額一律 Decimal/整數元（core/money），稅於發票總額層級推算一次（不逐項算稅）。
"""

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.audit import write_audit_log
from app.core.money import discounted_price, round_ntd, split_tax_inclusive
from app.modules.campaigns.models import Campaign
from app.modules.campaigns.service import CampaignService
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.service import ConsignmentService
from app.modules.contacts.service import ContactService
from app.modules.einvoice.service import EInvoiceService
from app.modules.inventory.service import InventoryService
from app.modules.menu.models import MenuItem
from app.modules.menu.service import MenuService
from app.modules.sales.inputs import InvoiceInfoInput, SaleLineInput, TenderInput
from app.modules.sales.linepay import (
    RETURN_CODE_ALREADY_REFUNDED,
    LinePayClient,
    linepay_order_id,
)
from app.modules.sales.models import (
    LinePayRefundAttempt,
    LinePayTransaction,
    Sale,
    SaleLine,
    SaleTender,
)
from app.modules.sales.repository import SalesRepository
from app.modules.settings.models import StoreSettings
from app.modules.settings.service import StoreSettingsService
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.service import UserService
from app.shared.enums import (
    CashMovementType,
    InvoiceType,
    ItemKind,
    LinePayRefundStatus,
    LinePayStatus,
    OwnershipType,
    PaymentMethod,
    SaleInvoiceStatus,
    SaleLineType,
    SaleStatus,
    StockReason,
    StoreCreditSourceType,
    TenderType,
)
from app.shared.exceptions import (
    CrossStoreReference,
    EInvoiceSettingsChanged,
    EmptySale,
    IdempotencyKeyConflict,
    InvalidSaleTender,
    InvalidStateTransition,
    LinePayChargeFailed,
    LinePayRefundAmbiguous,
    ManualRefundRequired,
    MenuItemNotFound,
    MenuItemUnavailable,
    NoOpenCashSession,
    SaleAlreadyVoid,
    SaleHasReturns,
    SaleItemNotFound,
    SaleLineInvalid,
    SignatureContentMismatch,
    SignatureTaskConflict,
)

_POINTS_DIVISOR = Decimal(100)  # 會員點數：floor(含稅總額 ÷ 100)，docs/16 §0


@dataclass(frozen=True)
class MarginBreakdown:
    """期間銷售/毛利彙總（未作廢；docs/19 §2.3 / R5）。R2/R5/R6 共用的單一口徑。

    - gross_turnover：營業額（所有成交全額，含寄售全額）。
    - recognized_revenue：認列營收（自有商品全額 + 寄售只認抽成；§7.3）。
    - gross_margin：毛利＝自有(售價−成本) + 寄售抽成；成本未知（catalog/缺成本自有）不計毛利。
    - gross_margin_rate：毛利 ÷ 已知成本營收（排除 unknown_cost_sales）；分母 0 → None。
    - unknown_cost_sales：成本未建模/未知的營收（catalog + 缺成本自有），不假造毛利。
    金額皆整數元；rate 為比率（4 位小數）或 None。
    """

    gross_turnover: Decimal
    recognized_revenue: Decimal
    owned_cogs: Decimal  # 自有序號成本
    bulk_cogs: Decimal  # 自有散裝成本
    consignment_commission_income: Decimal
    gross_margin: Decimal
    gross_margin_rate: Decimal | None
    unknown_cost_sales: Decimal
    cash_received: Decimal
    store_credit_redeemed: Decimal
    transaction_count: int
    # 餐飲/二手分列（裁示）：food_revenue=餐飲認列營收；secondhand_revenue=非餐飲認列營收
    # （＝recognized_revenue − food_revenue，含二手買斷/寄售抽成/數量品）。
    food_revenue: Decimal
    secondhand_revenue: Decimal
    # 支付手續費（docs/30 §7 決策 1）：手續費為店家成本、獨立支出行——認列營收/gross_margin 不含，
    # 另提供 net_margin = gross_margin − payment_fee_total。payment_methods 依 tender 分列各方式
    # (方法, 收款額, 手續費)。已作廢單的手續費不計（tender 查詢排除 VOID）。
    payment_fee_total: Decimal
    net_margin: Decimal
    payment_methods: tuple[tuple[str, Decimal, Decimal], ...]


@dataclass(frozen=True)
class _AppliedDiscount:
    """單行套用折扣後的結果（docs/21 C2）。unit_price 為折後實際成交單價。"""

    unit_price: Decimal  # 折後（無折扣＝原價）
    original_unit_price: Decimal | None  # 折前單價（無折扣→None，sale_line 留痕）
    discount_per_unit: Decimal  # 每件折讓（無折扣＝0）
    campaign_id: int | None


def _compute_discount(
    campaign: Campaign | None, original_unit: Decimal, *, applies: bool
) -> _AppliedDiscount:
    """依生效活動算折後單價；不適用（無活動/該品項未開）→ 原價、無留痕。"""
    if campaign is None or not applies:
        return _AppliedDiscount(original_unit, None, Decimal(0), None)
    disc = Decimal(discounted_price(original_unit, campaign.discount_pct))
    return _AppliedDiscount(disc, original_unit, original_unit - disc, campaign.id)


def _campaign_applies(
    campaign: Campaign | None, *, line_type: SaleLineType, is_consignment: bool
) -> bool:
    """該活動是否套用到此明細（docs/21 §8.2；process 與 quote 共用，避免口徑漂移）。

    序號：自有→applies_owned_serialized、寄售→applies_consignment；數量品→applies_catalog；
    散裝：只折自有（applies_owned_bulk），寄售散裝無抽成模型、永不折。
    """
    if campaign is None:
        return False
    if line_type == SaleLineType.MENU:
        return False  # 餐飲不參與門市活動折扣（裁示）
    if line_type == SaleLineType.SERIALIZED:
        return campaign.applies_consignment if is_consignment else campaign.applies_owned_serialized
    if line_type == SaleLineType.CATALOG:
        return campaign.applies_catalog
    return campaign.applies_owned_bulk and not is_consignment


@dataclass(frozen=True)
class QuoteLine:
    """結帳前試算的單行（唯讀；折後實際成交）。"""

    line_type: SaleLineType
    description: str
    qty: int
    unit_price: Decimal
    line_total: Decimal
    original_unit_price: Decimal | None
    discount_amount: Decimal


@dataclass(frozen=True)
class SaleQuote:
    """結帳前試算（docs/21 C2b）：套用生效活動後的折後總額與各行折讓；唯讀，不動庫存/不收款。

    供 POS 顯示折後價並送出對齊折後總額的收款（前端不自算金額）。

    food_subtotal：餐飲（內用）折後小計；store_credit_max：購物金最多可折抵額
    （＝total − food_subtotal，內用不得以購物金折抵）。POS 據此把購物金輸入卡在上限內。
    store_credit_min_spend：購物金低消門檻（整數元，0＝不限）；非餐飲消費（store_credit_max）
    未達此值則完全不可用購物金，POS 據此提示。
    """

    total: Decimal
    campaign_id: int | None
    campaign_name: str | None
    lines: list[QuoteLine]
    food_subtotal: Decimal
    store_credit_max: Decimal
    store_credit_min_spend: Decimal


def _member_points_for(total: Decimal) -> int:
    """該筆銷售累積的會員點數（floor；total 為含稅整數元，與 tender 組成無關）。"""
    return int(total // _POINTS_DIVISOR)


def _cart_fingerprint(
    lines: list[SaleLineInput],
    buyer_contact_id: int | None,
    tenders: list[TenderInput] | None = None,
    invoice_info: InvoiceInfoInput | None = None,
) -> str:
    """購物車＋收款＋發票資訊組成的穩定 sha256；供 idempotency 重播時比對請求是否相同。

    tenders 納入指紋：同 key 但收款組成不同（影響現金/帳本副作用）→ 視為不同請求。
    invoice_info 納入指紋（docs/24）：同 key 但統編/載具/捐贈不同（影響發票內容）→ 不同請求。
    """
    canonical = {
        "invoice_info": (
            None
            if invoice_info is None
            else {
                "buyer_tax_id": invoice_info.buyer_tax_id,
                "buyer_name": invoice_info.buyer_name,
                "carrier_type": invoice_info.carrier_type,
                "carrier_id": invoice_info.carrier_id,
                "npoban": invoice_info.npoban,
            }
        ),
        "buyer_contact_id": buyer_contact_id,
        "lines": [
            {
                "line_type": line.line_type.value,
                "item_code": line.item_code,
                "catalog_product_id": line.catalog_product_id,
                "bulk_lot_id": line.bulk_lot_id,
                "menu_item_id": line.menu_item_id,
                "qty": line.qty,
            }
            for line in lines
        ],
        # 收款組成納入指紋，但對 tender_type 正規化排序（順序不影響語意：每型別至多一筆）。
        "tenders": (
            None
            if tenders is None
            else sorted(
                ({"tender_type": t.tender_type.value, "amount": str(t.amount)} for t in tenders),
                key=lambda d: d["tender_type"],
            )
        ),
    }
    payload = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SalesService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        refund_ledger_sessionmaker: "async_sessionmaker[AsyncSession] | None" = None,
    ) -> None:
        self._session = session
        self._repo = SalesRepository(session)
        self._inventory = InventoryService(session)
        self._cash = CashDrawerService(session)
        self._consignment = ConsignmentService(session)
        self._settings = StoreSettingsService(session)
        self._users = UserService(session)
        self._contacts = ContactService(session)
        self._storecredit = StoreCreditService(session)
        self._campaigns = CampaignService(session)
        self._menu = MenuService(session)
        self._einvoice = EInvoiceService(session)
        # 退款對帳日誌用的**獨立** sessionmaker（docs/30 finding #1）：其提交獨立於主交易，
        # 故能在退貨/作廢回滾後存活，是唯一能防「呼叫平台 refund 後崩潰」重退的依據。預設用
        # 全域 sessionmaker（正式獨立連線）；測試可注入綁測試連線者以隨測試回滾。
        self._refund_ledger_sm = refund_ledger_sessionmaker

    @staticmethod
    def _normalize_tenders(tenders: list[TenderInput] | None) -> list[TenderInput] | None:
        """service 邊界守衛（直呼/raw 也擋）：非空、型別不重複、金額正整數。

        省略（None）→ 維持 None，由 _resolve_tenders 預設單一 CASH 全額。
        """
        if tenders is None:
            return None
        if not tenders:
            raise InvalidSaleTender("tenders 不可為空陣列（省略才表示預設現金全額）")
        seen: set[TenderType] = set()
        for t in tenders:
            if t.tender_type in seen:
                raise InvalidSaleTender(f"收款型別 {t.tender_type.value} 重複，每種至多一筆")
            seen.add(t.tender_type)
            if t.amount != t.amount.to_integral_value():
                raise InvalidSaleTender("收款金額必須為整數元")
            if t.amount <= 0:
                raise InvalidSaleTender("收款金額必須為正")
        return tenders

    @staticmethod
    def _resolve_tenders(total: Decimal, tenders: list[TenderInput] | None) -> list[TenderInput]:
        """把收款計畫對齊 total：省略 → 單一 CASH 全額；提供 → Σ amount 必須等於 total。"""
        if tenders is None:
            return [TenderInput(tender_type=TenderType.CASH, amount=total)]
        paid = sum((t.amount for t in tenders), Decimal(0))
        if paid != total:
            raise InvalidSaleTender(f"收款總額（{paid}）必須等於應付總額（{total}）")
        return tenders

    @staticmethod
    def _summary_payment_method(plan: list[TenderInput]) -> PaymentMethod:
        """sales.payment_method 摘要：單一 tender → 該型別、多 tender → MIXED。"""
        if len(plan) == 1:
            return PaymentMethod(plan[0].tender_type.value)
        return PaymentMethod.MIXED

    @staticmethod
    def _signed_amount(content: dict[str, object], key: str) -> Decimal | None:
        """自 STORE_CREDIT_USE 快照嚴格解析金額欄（非負整數元）；缺/小數/負/非數 → None。"""
        raw = content.get(key)
        if raw is None:
            return None
        try:
            d = Decimal(str(raw))
        except (ValueError, ArithmeticError):
            return None
        if d < 0 or d != d.to_integral_value():
            return None
        return d

    async def _bind_store_credit_signature(
        self,
        store_id: int,
        sale: Sale,
        store_credit_amount: Decimal,
        sale_total: Decimal,
        buyer_contact_id: int | None,
        signature_task_id: int | None,
        settings: object,
    ) -> tuple[Decimal, Decimal] | None:
        """購物金扣抵手持簽署綁定（docs/23 K5，D3）。純讀驗證＋設 sale.signature_task_id，
        先於任何收款副作用；單次使用由 uq_sales_signature_task 於 flush 守護。

        回傳簽署的餘額快照 (balance_before, balance_after)（有綁定時）供呼叫端於
        _apply_tenders **之後**（cash→credit 鎖序就位、帳戶列已鎖）做鎖定比對；無綁定回 None。
        """
        if store_credit_amount <= 0:
            # 未以購物金付款卻帶簽署 → 拒（購物金扣抵簽署僅適用購物金結帳）。
            if signature_task_id is not None:
                raise SignatureContentMismatch("未以購物金付款，不可綁定購物金扣抵簽署")
            return None

        if signature_task_id is None:
            require = getattr(settings, "require_store_credit_signing", False)
            if require:
                raise SignatureContentMismatch(
                    "本店已要求購物金扣抵須手持簽名確認：請先送至手持裝置由客人簽署後再結帳"
                )
            return None

        from app.modules.signing.service import SigningService

        assert buyer_contact_id is not None  # store_credit_amount>0 已保證有買方
        task = await SigningService(self._session).get_signed_store_credit_task(
            store_id, signature_task_id, contact_id=buyer_contact_id
        )
        signed_debit = self._signed_amount(task.content, "debit")
        if signed_debit is None or signed_debit != store_credit_amount:
            raise SignatureContentMismatch(
                "結帳的購物金折抵額與已簽確認不符，請重新推送簽署"
            )
        # 客人手持端看到並簽的**本次消費合計**也須與實際結帳總額精確相符（Codex K5 第二輪）：
        # 否則同折抵額換更大的購物車（現金補差）仍可綁定，留下描述不同交易的簽名證據。
        # 依店主裁示只綁客人看得到的（debit＋sale_total），購物車內部明細不綁。
        signed_total = self._signed_amount(task.content, "sale_total")
        if signed_total is None or signed_total != sale_total:
            raise SignatureContentMismatch(
                "結帳總額與已簽確認（本次消費合計）不符，請重新推送簽署"
            )
        # 客人簽的**餘額快照**（目前餘額/折抵後剩餘）也不得漂移（Codex K5 第六輪 high）：
        # 此處只做純解析（缺欄/非整數元即拒）；**與帳本的鎖定比對延後到 _apply_tenders 之後**
        # ——那時現金已先落地（cash_session 行鎖已持有）、DEBIT 已鎖帳戶列，維持全域
        # cash→credit 鎖序，避免與 SPLIT 收購（同 contact）AB-BA 死結（Codex K5 第十輪 high）。
        # 比對失敗在同一交易內拋例外 → 全部回滾，無半套。
        signed_before = self._signed_amount(task.content, "balance_before")
        signed_after = self._signed_amount(task.content, "balance_after")
        if signed_before is None or signed_after is None:
            raise SignatureContentMismatch(
                "已簽確認缺少餘額快照（目前餘額/折抵後剩餘），請重新推送簽署"
            )
        sale.signature_task_id = signature_task_id
        return (signed_before, signed_after)

    async def create_sale(
        self,
        store_id: int,
        clerk_user_id: int,
        *,
        lines: list[SaleLineInput],
        buyer_contact_id: int | None = None,
        tenders: list[TenderInput] | None = None,
        idempotency_key: str | None = None,
        signature_task_id: int | None = None,
        invoice_info: InvoiceInfoInput | None = None,
        expected_einvoice_enabled: bool | None = None,
        require_einvoice_confirmation: bool = False,
        linepay_client: LinePayClient | None = None,
    ) -> Sale:
        """建立銷售單並完成扣庫存/收款/結算；任一步失敗整筆回復（不 commit）。

        收款（SC-3，docs/16 §3.2）：tenders 省略 → 單一 CASH 全額（向後相容）；提供時
        Σ amount 必須等於 total。CASH tender → 錢櫃 SALE_IN（現金部分）；STORE_CREDIT
        tender → 帳本 DEBIT（買方購物金，餘額不足 → InsufficientStoreCredit 整筆回滾）。

        idempotency（D-2）：帶 idempotency_key 時，若同 (store_id, key) 已有銷售 → 直接回原單、
        不重跑任何副作用（防網路重試重複建單/收錢）。並行重送的競態由 sales 的
        (store_id, idempotency_key) 唯一約束在 flush/commit 擋下，由呼叫端據此回原單。
        """
        if not lines:
            raise EmptySale("銷售單必須至少有一筆明細")

        normalized_tenders = self._normalize_tenders(tenders)
        fingerprint = _cart_fingerprint(lines, buyer_contact_id, normalized_tenders, invoice_info)

        # idempotent replay：已存在同 key 的銷售 → 內容相同回原單、不再產生副作用；
        # 內容不同則拒絕（避免誤用/重用 key 把不同購物車的結帳靜默丟掉）。
        if idempotency_key is not None:
            replay = await self.find_idempotent_replay(
                store_id,
                idempotency_key,
                lines=lines,
                buyer_contact_id=buyer_contact_id,
                tenders=normalized_tenders,
                invoice_info=invoice_info,
            )
            if replay is not None:
                return replay

        # 簽署綁定的**前置**回放（Codex K5 第一輪；同 K4 第十輪教訓）：須先於任何當前狀態檢查
        # （開帳/庫存），否則首次已 commit、回應遺失後抽屜關帳等會讓重試在插入前就失敗、永遠
        # 走不到回放。既有綁定＝指紋相符回原單、不符/已作廢 → 409。並發首寫競態由 router 的
        # IntegrityError 兜底（兩請求都通過前置、插入時互撞）。
        if signature_task_id is not None:
            bound = await self._repo.get_by_signature_task_id(
                store_id, signature_task_id, for_update=True
            )
            if bound is not None:
                return await self.find_signature_replay(
                    store_id,
                    signature_task_id,
                    lines=lines,
                    buyer_contact_id=buyer_contact_id,
                    tenders=normalized_tenders,
                    invoice_info=invoice_info,
                )

        has_cash = normalized_tenders is None or any(
            t.tender_type == TenderType.CASH for t in normalized_tenders
        )
        has_store_credit = normalized_tenders is not None and any(
            t.tender_type == TenderType.STORE_CREDIT for t in normalized_tenders
        )
        line_pay_tenders = [
            t
            for t in (normalized_tenders or [])
            if t.tender_type == TenderType.LINE_PAY
        ]
        # LINE Pay 收款前置守衛（docs/30 P2；先於動庫存/收款）：
        # ①冪等鍵必填——orderId 由冪等鍵確定性導出（非 sale.id），rollback/retry 恆同號、
        #   先 check(orderId) 防重複扣款。無鍵則無法安全重試 → 擋。
        # ②每筆 LINE_PAY 須帶 oneTimeKey（掃客人碼）。③client 必須注入（router 依 config 建）。
        if line_pay_tenders:
            if idempotency_key is None:
                raise InvalidSaleTender("LINE Pay 收款必須帶冪等鍵（Idempotency-Key）")
            if any(t.line_pay_one_time_key is None for t in line_pay_tenders):
                raise InvalidSaleTender("LINE Pay 收款必須帶一次性付款碼（掃客人條碼）")
            if linepay_client is None:
                raise LinePayChargeFailed("LINE Pay 尚未設定（缺 Channel 憑證），無法收款")
        # 購物金付款必須有買方（扣誰的購物金）；於動任何庫存前就擋（§3.2、I-8）。
        if has_store_credit and buyer_contact_id is None:
            raise InvalidSaleTender("以購物金付款必須指定買方會員（buyer_contact_id）")
        # 收現必須在開帳中（§7.8）：最先檢查，避免動了庫存才發現不能收錢。純購物金
        # 付款不碰現金（I-9），不要求開帳。
        if has_cash and await self._cash.get_current_session(store_id) is None:
            raise NoOpenCashSession("結帳收現必須在開帳中的 cash_session 下進行，請先開帳")

        # 多分店資料隔離（§4）：clerk 與 buyer 都必須屬於本店，擋下跨店引用。
        if await self._users.get_user_in_store(store_id, clerk_user_id) is None:
            raise CrossStoreReference(f"clerk {clerk_user_id} 不屬於 store {store_id}")
        if buyer_contact_id is not None and (
            await self._contacts.get_contact(store_id, buyer_contact_id) is None
        ):
            raise CrossStoreReference(f"buyer contact {buyer_contact_id} 不屬於 store {store_id}")

        # 發票設定確認（docs/24；Codex 第廿二〜廿四輪）：**一律**先取該店設定的交易級共享
        # 鎖，再讀設定——鎖持有至本交易 commit，與並發 PATCH（writer）互斥，使
        # 「read→發票決策→commit」期間設定不可被改，杜絕 TOCTOU（read/writer 讓並發結帳
        # 彼此不阻塞）。設定於**動任何庫存/收款之前**讀一次、全程沿用同份（免請求內漂移）。
        await self._settings.lock_store_shared(store_id)
        settings = await self._settings.get_effective_settings(store_id)
        # LINE Pay 功能閘門（docs/30）：未啟用即拒帶 LINE_PAY tender 的結帳（fail-closed，
        # 先於任何庫存/收款副作用）。設定於上方共享鎖下讀取、全程沿用同份（免請求內漂移）。
        if line_pay_tenders and not settings.linepay_enabled:
            raise LinePayChargeFailed("本店未啟用 LINE Pay 收款（請於設定頁啟用）")
        # fail-closed（Codex 第廿四輪）：einvoice 啟用時，**HTTP 客戶端**必須帶
        # expected_einvoice_enabled 宣告其觀察值——省略者不得靜默開出預設 B2C（會漏收
        # 統編/載具/捐贈）。由 router 設 require_einvoice_confirmation=True 於 HTTP 邊界
        # 強制（真實攻擊面）；受信任的內部呼叫（其他 service/測試）不強制、維持彈性。
        # 前端一律帶；版本落後/直呼 HTTP 客戶端省略 → 409。
        if (
            require_einvoice_confirmation
            and settings.einvoice_enabled
            and expected_einvoice_enabled is None
        ):
            raise EInvoiceSettingsChanged(
                "本店已啟用電子發票：結帳須宣告發票設定狀態（請更新收銀端或重新整理）"
            )
        # 觀察值與現值不符（他端於前端刷新與 POST 間切換設定）→ 409，先於任何副作用。
        if (
            expected_einvoice_enabled is not None
            and settings.einvoice_enabled != expected_einvoice_enabled
        ):
            raise EInvoiceSettingsChanged(
                "電子發票設定於結帳期間變更"
                f"（目前{'啟用' if settings.einvoice_enabled else '停用'}），"
                "請重新確認發票欄位後再結帳"
            )
        # 防禦縱深：帶了發票欄位卻停用電子發票 → 拒絕（不靜默丟棄客人的統編/載具/捐贈而
        # 開出未開發票的單）。
        if invoice_info is not None and not settings.einvoice_enabled:
            raise EInvoiceSettingsChanged(
                "本店未啟用電子發票，但結帳帶了發票欄位（統編/載具/捐贈）；"
                "請確認發票設定後再結帳"
            )

        sale = await self._repo.add_sale(
            Sale(
                store_id=store_id,
                idempotency_key=idempotency_key,
                idempotency_fingerprint=fingerprint,
                clerk_user_id=clerk_user_id,
                buyer_contact_id=buyer_contact_id,
                subtotal=Decimal(0),
                tax=Decimal(0),
                total=Decimal(0),
            )
        )

        total = Decimal(0)
        # 售出的寄售品 → 稍後建 PENDING 結算：(序號品 id, 售價, 抽成百分數)
        consignment_sales: list[tuple[int, Decimal, int]] = []

        # 並發鎖序：先依 id 升冪鎖定本單序號品列，與作廢的 id 序退場一致；之後逐行（購物車序）
        # 的 sell 只再觸碰已持有的鎖。避免多件、反序銷售與並行作廢互卡 AB-BA 死結（Codex F6.5）。
        serialized_codes = [
            line.item_code
            for line in lines
            if line.line_type == SaleLineType.SERIALIZED and line.item_code is not None
        ]
        await self._inventory.prelock_serialized_for_sale(store_id, serialized_codes)

        # 門市活動折扣（docs/21 C2）：結帳當下取生效中活動（status=ACTIVE 且 now ∈ 窗），
        # 逐行依品項種類/擁有型態與活動開關套折後價（無活動→原價）。
        campaign = await self._campaigns.get_effective(store_id, datetime.now(UTC))

        food_subtotal = Decimal(0)  # 餐飲（內用）小計：購物金折抵上限與會員點數都要扣掉它
        for line in lines:
            line_total = await self._process_line(
                store_id, sale.id, line, consignment_sales, campaign
            )
            total += line_total
            if line.line_type == SaleLineType.MENU:
                food_subtotal += line_total

        # 零/負總額拒（§6 金額為正整數元）：每筆 tender 金額須 >0（DB CHECK），零總額會
        # 落到「無收款腿或 amount=0」的不合法狀態；免費出貨應走獨立流程，不借道銷售。
        if total <= 0:
            raise InvalidSaleTender("銷售總額必須大於 0（免費出貨請走獨立流程）")

        # 收款計畫對齊 total（Σ tenders 必須 = total，否則 422）。
        plan = self._resolve_tenders(total, normalized_tenders)

        # 內用不得以購物金折抵（裁示）：購物金 tender ≤ 應付總額 − 餐飲小計。
        store_credit_amount = sum(
            (t.amount for t in plan if t.tender_type == TenderType.STORE_CREDIT), Decimal(0)
        )
        redeemable_max = total - food_subtotal
        if store_credit_amount > redeemable_max:
            raise InvalidSaleTender(
                f"購物金最多折抵 {redeemable_max} 元（內用 {food_subtotal} 元不得以購物金折抵）"
            )

        # 購物金低消門檻（彈性設定，預設 0＝不限）：非餐飲消費未達門檻則完全不可用購物金。
        # settings 已於動庫存前讀取、沿用同一份（見上）。
        if store_credit_amount > 0 and redeemable_max < settings.store_credit_min_spend:
            raise InvalidSaleTender(
                f"未達購物金低消門檻：非餐飲消費需滿 {settings.store_credit_min_spend} 元"
                f"才能折抵購物金（目前 {redeemable_max} 元）"
            )

        # 購物金扣抵手持簽署綁定（docs/23 K5，D3）：以購物金付款時，若帶 signature_task_id 則驗證
        # 其為本店本買方之已簽 STORE_CREDIT_USE 任務，且**簽署的折抵額＋本次消費合計都與實際結帳
        # 精確相符**（客人手持端看到並簽的就是這筆交易）；單次使用由 uq_sales_signature_task 守護。
        # 政策開啟後未帶即擋。純讀驗證＋設欄，先於任何收款副作用（_apply_tenders）。
        signed_balance = await self._bind_store_credit_signature(
            store_id,
            sale,
            store_credit_amount,
            total,
            buyer_contact_id,
            signature_task_id,
            settings,
        )

        # 稅於發票總額層級推算一次（§6）；不逐項算稅。
        net, tax = split_tax_inclusive(total, settings.tax_rate)
        sale.subtotal = Decimal(net)
        sale.tax = Decimal(tax)
        sale.total = total
        sale.payment_method = self._summary_payment_method(plan)
        await self._session.flush()

        # 收款副作用（§3.2）：現金 tender → 錢櫃 SALE_IN（現金部分，非全額）；
        # 購物金 tender → 帳本 DEBIT（買方）。發票/稅/點數不受 tender 組成影響。
        await self._apply_tenders(
            store_id,
            sale,
            plan,
            clerk_user_id,
            buyer_contact_id,
            settings,
            idempotency_key=idempotency_key,
            linepay_client=linepay_client,
        )

        # 簽署餘額快照的鎖定比對（Codex K5 第六/十輪）：置於 _apply_tenders **之後**——現金已
        # 先落地（cash_session 行鎖持有）、DEBIT 已依全域 cash→credit 鎖序鎖住帳戶列，此處重鎖
        # 同列為 no-op，不會與 SPLIT 收購形成 AB-BA。扣抵後餘額必須等於客人簽的「折抵後剩餘」
        # （等價於簽署當下餘額未漂移）；不符即拋 → 同一交易全部回滾（含已記的現金/DEBIT）。
        if signed_balance is not None:
            from app.modules.storecredit.service import StoreCreditService

            assert buyer_contact_id is not None
            signed_before, signed_after = signed_balance
            final_balance = await StoreCreditService(self._session).get_balance_for_update(
                store_id, buyer_contact_id
            )
            drifted = signed_before != final_balance + store_credit_amount
            if final_balance != signed_after or drifted:
                raise SignatureContentMismatch(
                    "購物金餘額已變動，與已簽確認（目前餘額/折抵後剩餘）不符，請重新推送簽署"
                )

        # 寄售品 → 建 PENDING 結算（店家收入只認抽成，§7.3）。
        for serialized_item_id, gross, commission_pct in consignment_sales:
            await self._consignment.create_settlement(
                store_id,
                serialized_item_id=serialized_item_id,
                sale_id=sale.id,
                gross=gross,
                commission_pct=commission_pct,
            )

        # 會員點數累積（docs/16 §0）：floor(可累點金額/100)，同交易內、與銷售同生共死；
        # 內用不累點（裁示）→ 以「總額 − 餐飲小計」為基數；無買方不計；冪等重送回原單不重複累積。
        # 實際累積數記在 sale.awarded_points：void 以此沖回、不重算（規則改版/歷史單不錯沖）。
        if buyer_contact_id is not None:
            sale.awarded_points = _member_points_for(total - food_subtotal)
            await self._contacts.add_member_points(store_id, buyer_contact_id, sale.awarded_points)

        # 電子發票（§6）：einvoice_enabled 時於同一原子交易內建立**待開立（PENDING）**發票 +
        # F0401 上傳佇列，並標 sale.invoice_status=PENDING_ISSUE——尚未取得平台核可字軌號碼，
        # **非「已開立」**（配號/序列化/上傳待 T13 收尾，平台 ProcessResult 核可後才轉 ISSUED）。
        # 關閉時維持 NOT_ISSUED（銷售仍完整記錄、可日後補開）。買方統編未於 POS 收集 → 一律 B2C。
        # create_pending_invoice 以 sale_id 冪等；冪等重送於上方已回原單、不會重複開立。
        # 發票資訊（docs/24）：帶統編＝B2B；有載具或捐贈 → 不印證明聯（print_mark=False）。
        if settings.einvoice_enabled:
            info = invoice_info if invoice_info is not None else InvoiceInfoInput()
            is_b2b = info.buyer_tax_id is not None
            donate = info.npoban is not None
            has_carrier = info.carrier_type is not None and info.carrier_id is not None
            await self._einvoice.create_pending_invoice(
                store_id,
                sale_id=sale.id,
                total=total,
                tax_rate=settings.tax_rate,
                invoice_type=InvoiceType.B2B if is_b2b else InvoiceType.B2C,
                buyer_tax_id=info.buyer_tax_id,
                buyer_name=info.buyer_name,
                carrier_type=info.carrier_type if has_carrier else None,
                carrier_id=info.carrier_id if has_carrier else None,
                donate_mark=donate,
                npoban=info.npoban,
                print_mark=not donate and not has_carrier,
            )
            sale.invoice_status = SaleInvoiceStatus.PENDING_ISSUE

        await self._session.flush()
        return sale

    async def _apply_tenders(
        self,
        store_id: int,
        sale: Sale,
        plan: list[TenderInput],
        clerk_user_id: int,
        buyer_contact_id: int | None,
        settings: StoreSettings,
        *,
        idempotency_key: str | None = None,
        linepay_client: LinePayClient | None = None,
    ) -> None:
        """落地收款：現金入錢櫃 SALE_IN、購物金扣帳本 DEBIT、行動支付僅記 tender（非現金、不進
        抽屜，docs/30），並記 sale_tenders（含手續費快照）。

        固定 CASH 先於其他落地：建立 cash_session 與 store_credit_account 的**全域唯一鎖序**
        （與收購作廢的 cash→credit 一致），避免「購物金-先的混合銷售」與並行 SPLIT 作廢在同一
        contact 形成 AB-BA 死結（Codex F6.5 高風險）。各 tender 金額已固定、改順序不影響金額/紀錄。

        手續費（docs/30 裁示：獨立支出行）：LINE_PAY/TAIWAN_PAY 依 settings 費率於當下快照
        `fee = round_ntd(amount × fee_pct)`，記於 sale_tenders.fee_amount（店家成本，不減 amount）。
        LINE Pay 的 API 授權（fail-closed）由 P2 於此加入；本階段 TAIWAN_PAY 免 API。
        """
        for tender in sorted(plan, key=lambda t: 0 if t.tender_type == TenderType.CASH else 1):
            fee = Decimal(0)
            if tender.tender_type == TenderType.CASH:
                await self._cash.record_movement(
                    store_id,
                    CashMovementType.SALE_IN,
                    tender.amount,
                    actor_user_id=clerk_user_id,
                    ref_type="sale",
                    ref_id=sale.id,
                )
            elif tender.tender_type == TenderType.STORE_CREDIT:
                assert buyer_contact_id is not None  # 上方已於購物金付款時強制買方存在
                await self._storecredit.debit(
                    store_id,
                    buyer_contact_id,
                    amount=tender.amount,
                    source_type=StoreCreditSourceType.SALE,
                    source_id=sale.id,
                    created_by=clerk_user_id,
                )
            elif tender.tender_type == TenderType.TAIWAN_PAY:
                # 非現金、不進抽屜、無外部 API（店員於台灣Pay App 收款）；僅記手續費快照。
                fee = Decimal(round_ntd(tender.amount * settings.taiwanpay_fee_pct))
            elif tender.tender_type == TenderType.LINE_PAY:
                # 非現金、不進抽屜；手續費快照為店家成本。API 授權（fail-closed）見下。
                fee = Decimal(round_ntd(tender.amount * settings.linepay_fee_pct))
                assert idempotency_key is not None  # create_sale 已於前置守衛強制
                assert linepay_client is not None
                await self._charge_line_pay(
                    store_id, sale, tender, idempotency_key, linepay_client
                )
            await self._repo.add_tender(
                SaleTender(
                    store_id=store_id,
                    sale_id=sale.id,
                    tender_type=tender.tender_type,
                    amount=tender.amount,
                    fee_amount=fee,
                )
            )

    async def _charge_line_pay(
        self,
        store_id: int,
        sale: Sale,
        tender: TenderInput,
        idempotency_key: str,
        client: LinePayClient,
    ) -> None:
        """LINE Pay Offline v4 收款（fail-closed、冪等；docs/30 §4）。

        orderId 由 (store, 冪等鍵) 確定性導出——rollback/retry 恆同號。**check-first**：先向平台
        查此 orderId：
        - 已 COMPLETE（前次已扣款、回應在網路上遺失而本地回滾）→ **重用**該交易、不重扣。
        - 否則 → 呼叫 pay（消耗 oneTimeKey）。0000 成立、非 0000 → LinePayChargeFailed（整筆回滾）。
        傳輸錯誤（結果未知）沿 LinePayTransportError 上拋 → 整筆回滾（fail-closed，不留無付款單）。
        成立後記 linepay_transactions(COMPLETE)。order_id 唯一約束天然擋同單重扣。
        """
        assert tender.line_pay_one_time_key is not None  # create_sale 前置守衛已強制
        # orderId 綁金額（Codex finding #2）：同鍵不同金額必得不同 orderId，不會誤重用他額收款。
        order_id = linepay_order_id(
            store_id=store_id, idempotency_key=idempotency_key, amount=tender.amount
        )

        # check-first：平台已請款完成 → 重用（防重複扣款）。金額防呆（Codex finding #2）：orderId
        # 已綁金額，此處再比對平台回報的 payInfo 金額，任何不符即拒（不把他額收款當本次付款）。
        checked = await client.check(order_id=order_id)
        if checked.is_complete and checked.transaction_id is not None:
            if checked.amount is not None and checked.amount != tender.amount:
                raise LinePayChargeFailed(
                    f"LINE Pay 已存在同單號但金額不符的收款（平台 {checked.amount}、"
                    f"本次 {tender.amount}），拒絕重用；請人工至 LINE Pay 後台確認"
                )
            result = checked
        else:
            result = await client.pay(
                order_id=order_id,
                amount=tender.amount,
                one_time_key=tender.line_pay_one_time_key,
                product_name="門市消費",
            )
            if not result.is_success or result.transaction_id is None:
                raise LinePayChargeFailed(
                    f"LINE Pay 收款失敗（returnCode={result.return_code}），"
                    "整筆交易取消，請改用其他方式或重新掃碼"
                )

        await self._repo.add_linepay_transaction(
            LinePayTransaction(
                store_id=store_id,
                sale_id=sale.id,
                order_id=order_id,
                transaction_id=result.transaction_id,
                status=LinePayStatus.COMPLETE,
                amount=tender.amount,
                refunded_amount=Decimal(0),
                raw_response=result.raw,
            )
        )

    async def _refund_line_pay_for_sale(
        self, store_id: int, sale_id: int, client: LinePayClient | None
    ) -> None:
        """作廢時反轉 LINE Pay 收款（呼叫 refund；docs/30 §5）。

        非 LINE Pay 單或已退款（本地 REFUNDED）→ 冪等 no-op。以 orderId 退全額（amount−refunded）；
        平台 0000 或 1165（已退款）皆視為成功、標 REFUNDED、refunded_amount=amount。其餘 returnCode
        → LinePayChargeFailed；傳輸錯誤沿 LinePayTransportError——皆 fail-closed 使整筆作廢回滾，
        不留「已作廢卻未退款」的單。以 FOR UPDATE 鎖交易列與並發作廢/退款序列化。
        """
        txn = await self._repo.get_linepay_by_sale_id(store_id, sale_id, for_update=True)
        if txn is None or txn.status == LinePayStatus.REFUNDED:
            return
        refund_key = f"s{store_id}:void:{sale_id}"
        # 依 durable 日誌（依 order 累計）校準已退額（Codex 第三輪 #2）；未定 PENDING → 擋。
        await self._reconcile_refunds_from_ledger(store_id, txn, refund_key)
        remaining = txn.amount - txn.refunded_amount
        if remaining <= 0:
            txn.status = LinePayStatus.REFUNDED
            await self._session.flush()
            return
        if client is None:
            raise LinePayChargeFailed(
                "LINE Pay 尚未設定（缺 Channel 憑證），無法退款作廢；請設定後重試"
            )
        # 作廢全額退款走 durable 日誌防重退（Codex finding #1）；作廢一單一次；key 綁店別隔離。
        await self._durable_line_pay_refund(
            store_id=store_id,
            order_id=txn.order_id,
            refund_key=refund_key,
            amount=remaining,
            client=client,
        )
        await self._apply_ledger_truth(store_id, txn)

    async def refund_line_pay_amount(
        self,
        store_id: int,
        sale_id: int,
        amount: Decimal,
        client: LinePayClient | None,
        *,
        refund_key: str,
    ) -> bool:
        """對 LINE Pay 銷售退某金額（退貨部分退款；docs/30 §5）。跨模組經 service（§2）。

        退款總額真相以 durable 日誌（依 order 累計 SUCCEEDED）為準、非以 refund_key 逐筆加總；即使
        用了**不同的** refund_key（前次退貨崩潰回滾、店員改計畫重試），已成立退款仍計入，換鍵無法對
        同 order 超退（Codex #1/#2）。全退→REFUNDED、未全退→COMPLETE。回傳是否 LINE Pay 單。
        退款超原收款/未設定/平台拒/傳輸錯/有未定 PENDING → LinePayChargeFailed/TransportError/
        RefundAmbiguous（退貨整筆回滾，fail-closed）。
        """
        txn = await self._repo.get_linepay_by_sale_id(store_id, sale_id, for_update=True)
        if txn is None:
            return False
        # 校準已退額（補回已成立但回滾者）並取本 refund_key 是否已成立；未定 PENDING → 擋。
        _succeeded_total, this_key_done = await self._reconcile_refunds_from_ledger(
            store_id, txn, refund_key
        )
        if not this_key_done and txn.refunded_amount + amount > txn.amount:
            # 換鍵超退（本 key 尚未成立，且加上本額會超過原收款）→ 拒（可能前次已退，需人工對帳）。
            raise LinePayChargeFailed(
                f"LINE Pay 退款額超過原收款（原 {txn.amount}、已退 {txn.refunded_amount}）；"
                "此退貨可能已退款，請至 LINE Pay 後台確認後人工處理"
            )
        if client is None:
            raise LinePayChargeFailed("LINE Pay 尚未設定（缺 Channel 憑證），無法退款")
        await self._durable_line_pay_refund(
            store_id=store_id,
            order_id=txn.order_id,
            refund_key=refund_key,
            amount=amount,
            client=client,
        )
        await self._apply_ledger_truth(store_id, txn)
        return True

    async def _ledger_succeeded_total(self, store_id: int, order_id: str) -> Decimal:
        """該 (store, order) 全部 SUCCEEDED 退款累計（durable 真相）。"""
        from app.core.db import get_sessionmaker

        sm = self._refund_ledger_sm or get_sessionmaker()
        async with sm() as ledger:
            total = await ledger.scalar(
                select(func.coalesce(func.sum(LinePayRefundAttempt.amount), 0)).where(
                    LinePayRefundAttempt.store_id == store_id,
                    LinePayRefundAttempt.order_id == order_id,
                    LinePayRefundAttempt.status == LinePayRefundStatus.SUCCEEDED,
                )
            )
        return Decimal(total or 0)

    async def _reconcile_refunds_from_ledger(
        self, store_id: int, txn: LinePayTransaction, refund_key: str
    ) -> tuple[Decimal, bool]:
        """校準 refunded_amount（依 order 累計日誌），回 (SUCCEEDED 累計, 本 key 是否已成立)。

        退款真相以 append-only 日誌為準（跨主交易回滾存活）：查該 (store,order) SUCCEEDED 累計，
        大於本地 refunded_amount → 補回（prior 退款已成立但本地回滾）。任一 PENDING（含本 key、
        結果未定）→ LinePayRefundAmbiguous（fail-closed，需退款對帳頁解決）。this_key_done 供呼叫端
        判別「同 key 重試（已退、勿再加額）」vs「新退款」。
        """
        from app.core.db import get_sessionmaker

        sm = self._refund_ledger_sm or get_sessionmaker()
        async with sm() as ledger:
            has_pending = (
                await ledger.scalar(
                    select(func.count())
                    .select_from(LinePayRefundAttempt)
                    .where(
                        LinePayRefundAttempt.store_id == store_id,
                        LinePayRefundAttempt.order_id == txn.order_id,
                        LinePayRefundAttempt.status == LinePayRefundStatus.PENDING,
                    )
                )
            ) or 0
            succeeded_total = Decimal(
                (
                    await ledger.scalar(
                        select(func.coalesce(func.sum(LinePayRefundAttempt.amount), 0)).where(
                            LinePayRefundAttempt.store_id == store_id,
                            LinePayRefundAttempt.order_id == txn.order_id,
                            LinePayRefundAttempt.status == LinePayRefundStatus.SUCCEEDED,
                        )
                    )
                )
                or 0
            )
            this_key_done = (
                await ledger.scalar(
                    select(func.count())
                    .select_from(LinePayRefundAttempt)
                    .where(
                        LinePayRefundAttempt.store_id == store_id,
                        LinePayRefundAttempt.refund_key == refund_key,
                        LinePayRefundAttempt.status == LinePayRefundStatus.SUCCEEDED,
                    )
                )
            ) or 0
        if has_pending:
            raise LinePayRefundAmbiguous(
                "此 LINE Pay 訂單有結果未定的退款（可能已退款）：請至退款對帳頁確認/解決後再處理，"
                "勿直接重試以免超退"
            )
        if succeeded_total > txn.refunded_amount:
            # 補回「已成立但本地回滾」的退款額（不超過原收款；append-only 日誌不會超額）。
            txn.refunded_amount = min(succeeded_total, txn.amount)
            txn.status = (
                LinePayStatus.REFUNDED
                if txn.refunded_amount == txn.amount
                else LinePayStatus.COMPLETE
            )
            await self._session.flush()
        return succeeded_total, bool(this_key_done)

    async def _apply_ledger_truth(self, store_id: int, txn: LinePayTransaction) -> None:
        """退款成立後以 durable 日誌 SUCCEEDED 累計設 refunded_amount（真相；不逐筆加額防重複）。"""
        total = await self._ledger_succeeded_total(store_id, txn.order_id)
        txn.refunded_amount = min(total, txn.amount)
        txn.status = (
            LinePayStatus.REFUNDED
            if txn.refunded_amount == txn.amount
            else LinePayStatus.COMPLETE
        )
        await self._session.flush()

    async def list_pending_linepay_refunds(
        self, store_id: int
    ) -> list[LinePayRefundAttempt]:
        """結果未定（PENDING）的 LINE Pay 退款嘗試（退款對帳頁；Codex 第三輪 #3）。"""
        return await self._repo.list_pending_refund_attempts(store_id)

    async def resolve_linepay_refund(
        self,
        store_id: int,
        attempt_id: int,
        *,
        resolution: LinePayRefundStatus,
        actor_user_id: int,
    ) -> LinePayRefundAttempt:
        """人工解決一筆未定退款（Codex 第三輪 #3）：店長於 LINE Pay 後台確認後，把 PENDING 轉

        SUCCEEDED（已退款——後續退貨/作廢的依 order 累計對帳會據此補回 refunded_amount、不再重退）或
        FAILED（未退款——可安全重試）。僅 PENDING 可解；解決寫 audit_log（誰/何時/前後值）。
        """
        if resolution not in (LinePayRefundStatus.SUCCEEDED, LinePayRefundStatus.FAILED):
            raise InvalidSaleTender("退款解決結果只能為 SUCCEEDED（已退款）或 FAILED（未退款）")
        attempt = await self._repo.get_refund_attempt(store_id, attempt_id, for_update=True)
        if attempt is None:
            raise SaleItemNotFound(f"找不到退款對帳紀錄 {attempt_id}")
        if attempt.status != LinePayRefundStatus.PENDING:
            raise InvalidStateTransition(
                f"退款對帳紀錄 {attempt_id} 非未定狀態（{attempt.status.value}），不可再解決"
            )
        before = attempt.status.value
        attempt.status = resolution
        await self._session.flush()
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="RESOLVE_LINEPAY_REFUND",
            entity_type="linepay_refund_attempt",
            entity_id=str(attempt_id),
            before={"status": before},
            after={"status": resolution.value, "order_id": attempt.order_id},
        )
        return attempt

    async def _durable_line_pay_refund(
        self,
        *,
        store_id: int,
        order_id: str,
        refund_key: str,
        amount: Decimal,
        client: LinePayClient,
    ) -> None:
        """向平台送退款，以**獨立交易**的 append-only 日誌防重退（Codex adversarial finding #1）。

        三段：①查既有嘗試——SUCCEEDED→前次已退、直接返回不重退；PENDING→上次結果未定→
        LinePayRefundAmbiguous（fail-closed，人工對帳）；FAILED/無→續。②獨立提交 PENDING（其提交
        獨立於主交易，故主交易回滾後仍存活，是崩潰後判定 ambiguous 的依據）。③呼叫平台→獨立提交
        SUCCEEDED（0000/1165）或 FAILED。傳輸錯誤（結果未定）→保留 PENDING 並上拋，下次重試即
        ambiguous。非成功→標 FAILED 並拋 LinePayChargeFailed（可日後重試）。
        """
        from app.core.db import get_sessionmaker

        sm = self._refund_ledger_sm or get_sessionmaker()

        async with sm() as ledger:
            existing = await ledger.scalar(
                select(LinePayRefundAttempt).where(
                    LinePayRefundAttempt.store_id == store_id,
                    LinePayRefundAttempt.refund_key == refund_key,
                )
            )
            if existing is not None:
                # 內容綁定（Codex 第二輪 finding #1）：同 refund_key 既有紀錄的 (store, order, 額)
                # 必須與本次完全相符才可重用——否則是跨店同鍵碰撞或內容變更，重用會把別筆退款誤記成
                # 完成而不實際退款。不符即拒（不靜默重用錯列）。
                if (
                    existing.store_id != store_id
                    or existing.order_id != order_id
                    or existing.amount != amount
                ):
                    raise LinePayRefundAmbiguous(
                        "LINE Pay 退款對帳鍵與既有紀錄的店別/訂單/金額不符，拒絕重用；"
                        "請至 LINE Pay 後台確認後人工處理"
                    )
                if existing.status == LinePayRefundStatus.SUCCEEDED:
                    return  # 前次已退款成功 → 跳過不重退（防重退核心）
                if existing.status == LinePayRefundStatus.PENDING:
                    raise LinePayRefundAmbiguous(
                        "此筆 LINE Pay 退款上次結果未定（可能已退款）：請至 LINE Pay 後台確認"
                        "後再處理，勿直接重試以免超退"
                    )
                # FAILED → 可安全重試（往下）

        # Phase 1：獨立提交 PENDING（崩潰後重試據此判定 ambiguous）
        async with sm() as ledger:
            row = await ledger.scalar(
                select(LinePayRefundAttempt)
                .where(LinePayRefundAttempt.refund_key == refund_key)
                .with_for_update()
            )
            if row is None:
                ledger.add(
                    LinePayRefundAttempt(
                        store_id=store_id,
                        refund_key=refund_key,
                        order_id=order_id,
                        amount=amount,
                        status=LinePayRefundStatus.PENDING,
                    )
                )
            else:
                row.status = LinePayRefundStatus.PENDING
                row.return_code = None
            await ledger.commit()

        # Phase 2：呼叫平台（傳輸錯誤 → 保留 PENDING 並上拋，下次重試即 ambiguous）
        result = await client.refund(order_id=order_id, refund_amount=amount)
        succeeded = result.is_success or result.return_code == RETURN_CODE_ALREADY_REFUNDED

        # Phase 3：獨立提交終態
        async with sm() as ledger:
            row = await ledger.scalar(
                select(LinePayRefundAttempt)
                .where(LinePayRefundAttempt.refund_key == refund_key)
                .with_for_update()
            )
            if row is not None:
                row.status = (
                    LinePayRefundStatus.SUCCEEDED if succeeded else LinePayRefundStatus.FAILED
                )
                row.return_code = result.return_code
                await ledger.commit()
        if not succeeded:
            raise LinePayChargeFailed(
                f"LINE Pay 退款失敗（returnCode={result.return_code}），請稍後重試"
            )

    async def find_idempotent_replay(
        self,
        store_id: int,
        idempotency_key: str,
        *,
        lines: list[SaleLineInput],
        buyer_contact_id: int | None,
        tenders: list[TenderInput] | None = None,
        invoice_info: InvoiceInfoInput | None = None,
    ) -> Sale | None:
        """同 key 且購物車＋收款相符 → 回原單；內容不符 → IdempotencyKeyConflict；不存在 → None。

        pre-check（create_sale）與 router 的 IntegrityError handler（並行重送）共用此處，
        避免「修一條路徑、漏另一條」導致併發同 key 不同購物車仍被靜默當成功。
        """
        # 鎖列讀（K4 第十五輪同款）：回放決策與並行 void 序列化，不得讀到 void 前舊版誤回成功。
        existing = await self._repo.get_by_idempotency_key(
            store_id, idempotency_key, for_update=True
        )
        if existing is None:
            return None
        if existing.idempotency_fingerprint != _cart_fingerprint(
            lines, buyer_contact_id, tenders, invoice_info
        ):
            raise IdempotencyKeyConflict(
                f"idempotency key 已用於不同的購物車內容（sale {existing.id}）"
            )
        # 已作廢的銷售不可回放為成功（K4 第十四/十六輪同款）：作廢已反轉點數/寄售結算，
        # 回 201 會讓 POS 又開櫃/印明細、與已反轉帳本脫節 → 409。
        if existing.invoice_status is SaleInvoiceStatus.VOID:
            raise SaleAlreadyVoid(f"sale {existing.id} 已作廢，不可以原冪等鍵重放；請重新結帳")
        return existing

    async def find_signature_replay(
        self,
        store_id: int,
        signature_task_id: int,
        *,
        lines: list[SaleLineInput],
        buyer_contact_id: int | None,
        tenders: list[TenderInput] | None = None,
        invoice_info: InvoiceInfoInput | None = None,
    ) -> Sale:
        """簽署綁定的「回應遺失重試」回放（docs/23 K5，Codex 第一輪；同 K4 第九/十/十三/十五輪）。

        POS 冪等鍵存 ref、重掛會遺失；首次已 commit、回應遺失後以新鍵重試，無法以冪等鍵回放，
        只會撞 uq_sales_signature_task。若既有那筆銷售的購物車指紋與本次相同（同單重送）→ 回原
        單讓 POS 跑成功路徑、不重複扣購物金；指紋不同（拿別單的簽署硬綁）或已作廢 → 409。
        FOR UPDATE 鎖列與並行 void 序列化。
        """
        existing = await self._repo.get_by_signature_task_id(
            store_id, signature_task_id, for_update=True
        )
        if existing is None:
            raise SignatureTaskConflict("此購物金扣抵簽署結帳衝突，請重試")
        if existing.invoice_status is SaleInvoiceStatus.VOID:
            raise SignatureTaskConflict(
                "此扣抵簽署綁定的銷售已作廢，不可重放或重用；請重新推送簽署"
            )
        if existing.idempotency_fingerprint != _cart_fingerprint(
            lines, buyer_contact_id, tenders, invoice_info
        ):
            raise SignatureTaskConflict("此購物金扣抵簽署已綁定另一筆結帳，不可重複使用")
        return existing

    # ── 查詢 ──
    async def get_sale(self, store_id: int, sale_id: int) -> Sale | None:
        return await self._repo.get_sale(store_id, sale_id)

    async def get_sale_for_update(self, store_id: int, sale_id: int) -> Sale | None:
        """FOR UPDATE 鎖列取銷售：供跨模組（signing 簽收）與 void/return 的行鎖序列化。"""
        return await self._repo.lock_sale(store_id, sale_id)

    async def find_sale_by_signature_task(
        self, store_id: int, signature_task_id: int
    ) -> Sale | None:
        """已綁定某扣抵簽署的銷售（跨模組供 signing 判斷「已簽未綁」可否作廢；docs/23 K5）。"""
        return await self._repo.get_by_signature_task_id(store_id, signature_task_id)

    async def get_lines(self, sale_id: int) -> list[SaleLine]:
        return await self._repo.list_lines(sale_id)

    async def get_serialized_sale_line(
        self, store_id: int, serialized_item_id: int
    ) -> tuple[SaleLine, Sale] | None:
        """某序號品最近一筆銷售明細＋銷售單（庫存明細頁用；至多一筆，不變量 1）。"""
        return await self._repo.get_serialized_sale_line(store_id, serialized_item_id)

    async def get_tenders(self, sale_id: int) -> list[SaleTender]:
        return await self._repo.list_tenders(sale_id)

    async def list_sales(
        self,
        store_id: int,
        *,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
        offset: int,
    ) -> list[Sale]:
        return await self._repo.list_sales(
            store_id, date_from=date_from, date_to=date_to, limit=limit, offset=offset
        )

    async def list_purchases_by_buyer(
        self,
        store_id: int,
        contact_id: int,
        *,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Sale]:
        """某會員的消費紀錄（會員中心；store 範圍、可選日期區間、新到舊、分頁；docs/17 §5.2）。"""
        return await self._repo.list_sales_by_buyer(
            store_id,
            contact_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )

    async def count_purchases_by_buyer(self, store_id: int, contact_id: int) -> int:
        """某會員的消費總筆數（會員中心 overview headline）。"""
        return await self._repo.count_sales_by_buyer(store_id, contact_id)

    async def line_counts_for_sales(self, sale_ids: list[int]) -> dict[int, int]:
        """各銷售單明細行數（會員中心消費清單；單一查詢避免 N+1）。"""
        return await self._repo.count_lines_for_sales(sale_ids)

    # ── SC-5b §5B 唯讀彙總（供 storecredit 指標/引擎跨模組取數，§2 經 service）──

    async def period_margin(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> dict[str, Decimal]:
        """期間毛利拆解：revenue（商品收入）、buyout_margin（買斷毛利）、寄售抽成。

        買斷毛利＋商品收入由自有/寄售商品行推導（sales 經 inventory 成本，repo 唯讀 join）；
        寄售抽成經 consignment service 依未作廢 sale_id 取（§2）。m = (買斷毛利＋抽成) ÷ 收入。
        """
        # 已知限制（裁示 2026-07-16「其餘文件化」）：此處**不套**退貨扣減。period_margin
        # 僅供 SC-5b 溢價建議引擎（分析用、非帳務），D-8 退貨扣減已在 margin_breakdown
        # （R2/R5/R6/C4 主帳務口徑）落實；影響量在模擬中為全期營收 0.05%。若日後要一致，
        # 於此比照 margin_breakdown 減 ReturnsService.margin_adjustments 的買斷毛利/收入分量。
        buyout_margin, revenue = await self._repo.goods_margin_and_revenue(
            store_id, date_from, date_to
        )
        sale_ids = await self._repo.nonvoid_sale_ids(store_id, date_from, date_to)
        commission = await self._consignment.commission_total_for_sales(store_id, sale_ids)
        return {
            "revenue": revenue,
            "buyout_margin": buyout_margin,
            "consignment_commission": commission,
        }

    async def discount_totals_by_campaign(self, store_id: int) -> dict[int, Decimal]:
        """各活動實際造成的折讓總額（非作廢；供活動成效報表 C4，依 sale_line.campaign_id 歸屬）。"""
        return await self._repo.discount_totals_by_campaign(store_id)

    async def margin_breakdown(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> MarginBreakdown:
        """期間銷售/毛利彙總（單一口徑，R2/R5/R6 共用）。寄售抽成經 consignment service 取。

        退貨扣減（D-8(1)，裁示 2026-07-16）：依退貨行按比例自各營收/成本桶扣除，
        歸屬**退貨發生日**（與退現出帳同日）。經 returns service 取（§2 跨模組經 service；
        函式內 import 破 sales↔returns↔einvoice 循環）。
        """
        from app.modules.returns.service import ReturnsService

        comp = await self._repo.margin_components(store_id, date_from, date_to)
        adj = await ReturnsService(self._session).margin_adjustments(
            store_id, date_from, date_to
        )
        comp = replace(
            comp,
            owned_serialized_revenue=comp.owned_serialized_revenue
            - adj.owned_serialized_revenue,
            owned_serialized_cogs=comp.owned_serialized_cogs - adj.owned_serialized_cogs,
            owned_bulk_revenue=comp.owned_bulk_revenue - adj.owned_bulk_revenue,
            owned_bulk_cogs=comp.owned_bulk_cogs - adj.owned_bulk_cogs,
            consignment_serialized_revenue=comp.consignment_serialized_revenue
            - adj.consignment_serialized_revenue,
            consignment_bulk_revenue=comp.consignment_bulk_revenue
            - adj.consignment_bulk_revenue,
            catalog_revenue=comp.catalog_revenue - adj.catalog_revenue,
            unknown_cost_revenue=comp.unknown_cost_revenue
            - adj.catalog_revenue
            - adj.no_cost_serialized_revenue,
        )
        sale_ids = await self._repo.nonvoid_sale_ids(store_id, date_from, date_to)
        commission = await self._consignment.commission_total_for_sales(store_id, sale_ids)

        gross_turnover = (
            comp.owned_serialized_revenue
            + comp.owned_bulk_revenue
            + comp.consignment_serialized_revenue
            + comp.consignment_bulk_revenue
            + comp.unknown_cost_revenue
        )
        recognized_revenue = (
            comp.owned_serialized_revenue
            + comp.owned_bulk_revenue
            + comp.unknown_cost_revenue
            + commission
        )
        owned_margin = comp.owned_serialized_revenue - comp.owned_serialized_cogs
        bulk_margin = comp.owned_bulk_revenue - comp.owned_bulk_cogs
        gross_margin = owned_margin + bulk_margin + commission
        known_cost_revenue = comp.owned_serialized_revenue + comp.owned_bulk_revenue + commission
        rate: Decimal | None = (
            (gross_margin / known_cost_revenue).quantize(Decimal("0.0001"))
            if known_cost_revenue > 0
            else None
        )
        return MarginBreakdown(
            gross_turnover=gross_turnover,
            recognized_revenue=recognized_revenue,
            owned_cogs=comp.owned_serialized_cogs,
            bulk_cogs=comp.owned_bulk_cogs,
            consignment_commission_income=commission,
            gross_margin=gross_margin,
            gross_margin_rate=rate,
            unknown_cost_sales=comp.unknown_cost_revenue,
            cash_received=comp.cash_received,
            store_credit_redeemed=comp.store_credit_redeemed,
            transaction_count=comp.transaction_count,
            food_revenue=comp.menu_revenue,
            secondhand_revenue=recognized_revenue - comp.menu_revenue,
            payment_fee_total=comp.payment_fee_total,
            net_margin=gross_margin - comp.payment_fee_total,
            payment_methods=comp.payment_methods,
        )

    async def serialized_sold_rows(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> list[
        tuple[
            int | None,
            int | None,
            OwnershipType,
            Decimal | None,
            int | None,
            datetime,
            datetime | None,
            Decimal,
        ]
    ]:
        """期間售出序號品的洞察原始列（經營洞察報表逐品牌/類型彙整用）。"""
        return await self._repo.serialized_sold_rows(store_id, date_from, date_to)

    async def bulk_sold_rows(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> list[
        tuple[
            int | None,
            int | None,
            int | None,
            Decimal,
            int,
            int,
            datetime,
            datetime,
            Decimal,
        ]
    ]:
        """期間售出散裝的洞察原始列（經營洞察把散裝納入品牌/類型排行；Codex P2）。"""
        return await self._repo.bulk_sold_rows(store_id, date_from, date_to)

    async def excess_spend_components(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> dict[str, Decimal]:
        """含購物金 tender 的銷售：total（Σ含稅總額）、cash（Σ現金部分）；率=cash÷total。"""
        total, cash = await self._repo.excess_spend_components(store_id, date_from, date_to)
        return {"total": total, "cash": cash}

    async def member_purchase_count(
        self, store_id: int, contact_id: int, *, date_from: datetime, date_to: datetime
    ) -> int:
        """某會員在 [date_from, date_to) 的未作廢消費筆數（α 代理：CREDIT 入帳前 N 天消費紀錄）。"""
        return await self._repo.member_purchase_count(store_id, contact_id, date_from, date_to)

    # ── 作廢 ──
    async def void_sale(
        self,
        sale: Sale,
        actor_user_id: int,
        *,
        linepay_client: LinePayClient | None = None,
        manual_refund_ack: bool = False,
    ) -> Sale:
        """作廢銷售：標記 invoice_status=VOID（待作廢），寫稽核；不刪除、不在此反轉庫存/退現。

        若原銷售已開發票（invoice_status=ISSUED），此 VOID 為「作廢發票流程」的接縫——實際
        電子發票作廢 XML 由 T13/T14 處理。退現/折讓/庫存回補屬 Phase 4 returns（§7.5），不在此；
        但**寄售結算反轉**於此一併處理（invariant #7：未付→CANCELLED、已付→reclaim_needed），
        否則作廢後該結算仍 PENDING、可被付款給寄售人造成現金漏出（Codex adversarial round-2）。

        併發保證：先以 FOR UPDATE 鎖 sale 列並刷新到已提交狀態，再檢查/轉移（比照 D-1）；
        兩個並行作廢只一個成功，另一個鎖後見 VOID → SaleAlreadyVoid，稽核也只寫一筆。
        """
        locked = await self._repo.lock_sale(sale.store_id, sale.id)
        if locked is None or locked.invoice_status == SaleInvoiceStatus.VOID:
            raise SaleAlreadyVoid(f"sale {sale.id} 已作廢，不可重複作廢")
        sale = locked
        # 已退貨的銷售不可作廢（Codex P1）：退貨已回補庫存/退款，且退回的序號品可能已被
        # 後續銷售再賣出——若再作廢回補會重複放回庫存、或把別單賣掉的品翻回 IN_STOCK。
        # 函式內 import 打破 sales↔returns 潛在循環相依（§9 例外）。
        from app.modules.returns.service import ReturnsService

        if await ReturnsService(self._session).has_returns_for_sale(sale.store_id, sale.id):
            raise SaleHasReturns(f"sale {sale.id} 已有退貨，不可作廢；請以退貨流程處理剩餘部分")
        # 台灣Pay 無 API 退款（店員於其 App 手動退）：作廢不得靜默完成而讓客人仍被扣款（Codex
        # adversarial finding #3）。含 TAIWAN_PAY tender 者，須店員先手動退款、帶 manual_refund_ack
        # 確認才反轉。純 LINE Pay 由下方 refund API 自動退、無需此確認；現金於錢櫃取出（既有口徑）。
        tenders = await self._repo.list_tenders(sale.id)
        if (
            any(t.tender_type == TenderType.TAIWAN_PAY for t in tenders)
            and not manual_refund_ack
        ):
            raise ManualRefundRequired(
                "此單含台灣Pay 收款（無 API 退款）：作廢前請先於台灣Pay App 手動退款給客人，"
                "並勾選確認後再作廢，以免客人已作廢卻仍被扣款"
            )
        before = sale.invoice_status.value
        sale.invoice_status = SaleInvoiceStatus.VOID
        await self._session.flush()
        await write_audit_log(
            self._session,
            store_id=sale.store_id,
            actor_user_id=actor_user_id,
            action="VOID_SALE",
            entity_type="sale",
            entity_id=str(sale.id),
            before={"invoice_status": before},
            after={"invoice_status": SaleInvoiceStatus.VOID.value},
        )
        # 作廢沖回該筆「當時實際累積」的點數（awarded_points；歷史單為 0 → 不倒扣）。
        if sale.buyer_contact_id is not None and sale.awarded_points > 0:
            await self._contacts.add_member_points(
                sale.store_id, sale.buyer_contact_id, -sale.awarded_points
            )
        # 購物金 tender 沖回（§3.3）：DEBIT/SALE → REVERSAL/SALE_VOID（入回買方餘額）；
        # 無購物金 tender → no-op。沖正冪等：重複作廢路徑被 SaleAlreadyVoid 先擋，
        # 即便走到也只入回一次（同來源回原沖正列）。現金 tender 退現屬 Phase 4 returns。
        await self._storecredit.reverse_for_sale_void(
            sale.store_id, sale.id, created_by=actor_user_id
        )
        # LINE Pay 退款反轉（§5，invariant #5）：非現金外部收款，作廢＝呼叫 refund 反向沖回客人。
        # 非 LINE Pay 單 → no-op。退款失敗/傳輸錯誤 → fail-closed（拋出、整筆作廢回滾），不得留下
        # 已作廢卻未退款的單；已退款（本地 REFUNDED 或平台 1165）→ 冪等 no-op。
        await self._refund_line_pay_for_sale(sale.store_id, sale.id, linepay_client)
        # 電子發票中止（§6）：把該銷售的待開立發票標 VOID，使其待送佇列列被 drop_pending 拒絕，
        # 不會把已作廢銷售的發票拋上平台。已核可（ISSUED）發票的作廢須另送 F0501/F0701 平台訊息
        # ——該路徑待收尾階段依 作廢 vs 註銷 規則接線。無發票（einvoice 關閉時建的單）→ no-op。
        await self._einvoice.void_invoice_for_sale(sale.store_id, sale.id)
        # 寄售結算反轉（invariant #7，Phase 4）：未付→CANCELLED、已付→reclaim_needed，
        # 否則作廢後仍可付款給寄售人造成現金漏出（Codex adversarial）。非寄售單 → no-op。
        await self._consignment.cancel_settlements_for_sale(
            sale.store_id, sale.id, actor_user_id=actor_user_id
        )
        # 庫存回補（invariant #1/#6）：作廢＝此筆銷售視為未發生，須把賣出的庫存放回——
        # 序號品 SOLD→IN_STOCK、散裝 remaining 加回、數量品現量加回（與退貨同口徑，
        # 但不產生退貨單/折讓/退現）。否則作廢後庫存被永久消耗、序號品卡在 SOLD 不能再賣、
        # 散裝守恆破（B6）。只對「未退貨（COMPLETED）」的單回補，避免與退貨流程重複回補。
        if sale.status == SaleStatus.COMPLETED:
            for line in await self._repo.list_lines(sale.id):
                if line.line_type == SaleLineType.CATALOG and line.catalog_product_id is not None:
                    await self._inventory.return_catalog_items(
                        sale.store_id,
                        line.catalog_product_id,
                        line.qty,
                        ref_type="sale_void",
                        ref_id=sale.id,
                    )
                elif (
                    line.line_type == SaleLineType.SERIALIZED
                    and line.serialized_item_id is not None
                ):
                    await self._inventory.return_serialized_sale_item(
                        sale.store_id,
                        line.serialized_item_id,
                        ref_type="sale_void",
                        ref_id=sale.id,
                    )
                elif line.line_type == SaleLineType.BULK_LOT and line.bulk_lot_id is not None:
                    await self._inventory.return_bulk_lot_items(
                        sale.store_id,
                        line.bulk_lot_id,
                        line.qty,
                        ref_type="sale_void",
                        ref_id=sale.id,
                    )
                # MENU：無庫存，略過。
        return sale

    async def mark_invoice_issued(self, store_id: int, sale_id: int) -> None:
        """電子發票平台核可（F0401 ProcessResult 成功）後，把對應銷售的 invoice_status
        由 PENDING_ISSUE 轉 ISSUED（由 einvoice service 於回執處理時回呼；跨模組經 service，§2）。

        僅在仍為 PENDING_ISSUE 時轉移（冪等、且不覆寫已 VOID/ALLOWANCE 等後續狀態）。
        """
        sale = await self._repo.lock_sale(store_id, sale_id)
        if sale is not None and sale.invoice_status == SaleInvoiceStatus.PENDING_ISSUE:
            sale.invoice_status = SaleInvoiceStatus.ISSUED
            await self._session.flush()

    async def mark_invoice_allowance(self, store_id: int, sale_id: int) -> None:
        """G0401 折讓平台核可後，把對應銷售 invoice_status 由 PENDING_ALLOWANCE 轉 ALLOWANCE
        （einvoice service 回執處理時回呼；跨模組經 service，§2）。僅 PENDING_ALLOWANCE 時轉。"""
        sale = await self._repo.lock_sale(store_id, sale_id)
        if sale is not None and sale.invoice_status == SaleInvoiceStatus.PENDING_ALLOWANCE:
            sale.invoice_status = SaleInvoiceStatus.ALLOWANCE
            await self._session.flush()

    async def lock_sale_row(self, store_id: int, sale_id: int) -> Sale | None:
        """鎖定銷售列（FOR UPDATE；跨模組經 service，§2）。

        **全域鎖序 sale → queue**（Codex 第六輪）：作廢/退貨路徑天然先鎖 sale 再動 einvoice
        佇列；回執路徑（einvoice.record_result）在鎖佇列列**之前**必須先經此方法鎖關聯 sale，
        否則兩路徑 AB-BA 死鎖。同交易內重複鎖同列免費（mark_invoice_* 之後重鎖無害）。
        """
        return await self._repo.lock_sale(store_id, sale_id)

    async def mark_invoice_not_issued(self, store_id: int, sale_id: int) -> None:
        """待開立發票被中止（退貨觸發的作廢收斂：F0401 失敗或 F0501 核可）後，把對應銷售的
        invoice_status 由 PENDING_ISSUE 收斂回 NOT_ISSUED——該銷售最終無有效發票。

        僅在仍為 PENDING_ISSUE 時轉：sale-void 路徑的 invoice_status 已是 VOID（銷售作廢語意，
        報表據此排除，D-3），不得覆寫。einvoice service 回執處理時回呼（跨模組經 service，§2）。
        """
        sale = await self._repo.lock_sale(store_id, sale_id)
        if sale is not None and sale.invoice_status == SaleInvoiceStatus.PENDING_ISSUE:
            sale.invoice_status = SaleInvoiceStatus.NOT_ISSUED
            await self._session.flush()

    async def record_print_detail(self, sale: Sale, actor_user_id: int) -> None:
        """補印商品明細聯：寫稽核（實際列印由前端送硬體代理，見 docs/04、Phase 3 硬體）。"""
        await write_audit_log(
            self._session,
            store_id=sale.store_id,
            actor_user_id=actor_user_id,
            action="PRINT_SALE_DETAIL",
            entity_type="sale",
            entity_id=str(sale.id),
        )

    async def quote_sale(
        self,
        store_id: int,
        *,
        lines: list[SaleLineInput],
        buyer_contact_id: int | None = None,
    ) -> SaleQuote:
        """結帳前試算（docs/21 C2b）：套生效活動後的折後總額與各行折讓。唯讀——不扣庫存、不收款、
        不建單。供 POS 顯示折後價並送對齊折後總額的收款（避免前端自算金額、收款不對齊 → 422）。

        不執行 STORE_ABSORBS 的「折讓>抽成」虧損守衛（那是收款側守衛，由 create_sale 把關）；
        quote 只回客人實付的折後金額（兩種 bearing 客人實付相同）。
        """
        if not lines:
            raise EmptySale("結帳試算必須至少有一筆明細")
        campaign = await self._campaigns.get_effective(store_id, datetime.now(UTC))
        quoted: list[QuoteLine] = []
        total = Decimal(0)
        food_subtotal = Decimal(0)
        for line in lines:
            ql = await self._quote_line(store_id, line, campaign)
            quoted.append(ql)
            total += ql.line_total
            if ql.line_type == SaleLineType.MENU:
                food_subtotal += ql.line_total
        min_spend = (await self._settings.get_effective_settings(store_id)).store_credit_min_spend
        return SaleQuote(
            total=total,
            campaign_id=campaign.id if campaign is not None else None,
            campaign_name=campaign.name if campaign is not None else None,
            lines=quoted,
            food_subtotal=food_subtotal,
            store_credit_max=total - food_subtotal,
            store_credit_min_spend=min_spend,
        )

    async def _quote_line(
        self, store_id: int, line: SaleLineInput, campaign: Campaign | None
    ) -> QuoteLine:
        """單行試算（唯讀）：解析品項、算折後價；不動任何狀態。"""
        if line.line_type == SaleLineType.SERIALIZED:
            if line.item_code is None:
                raise SaleLineInvalid("SERIALIZED 明細必須帶 item_code")
            item = await self._inventory.get_serialized_by_code(store_id, line.item_code)
            if item is None:
                raise SaleItemNotFound(f"找不到序號品 {line.item_code}")
            applies = _campaign_applies(
                campaign,
                line_type=SaleLineType.SERIALIZED,
                is_consignment=item.ownership_type == OwnershipType.CONSIGNMENT,
            )
            disc = _compute_discount(campaign, item.listed_price, applies=applies)
            return QuoteLine(
                line_type=SaleLineType.SERIALIZED,
                description=item.name,
                qty=1,
                unit_price=disc.unit_price,
                line_total=disc.unit_price,
                original_unit_price=disc.original_unit_price,
                discount_amount=disc.discount_per_unit,
            )
        if line.line_type == SaleLineType.CATALOG:
            if line.catalog_product_id is None:
                raise SaleLineInvalid("CATALOG 明細必須帶 catalog_product_id")
            if line.qty <= 0:
                raise SaleLineInvalid("CATALOG 明細數量必須 > 0")
            product = await self._inventory.get_catalog(store_id, line.catalog_product_id)
            if product is None:
                raise SaleItemNotFound(f"找不到數量型商品 {line.catalog_product_id}")
            applies = _campaign_applies(
                campaign, line_type=SaleLineType.CATALOG, is_consignment=False
            )
            disc = _compute_discount(campaign, product.unit_price, applies=applies)
            return QuoteLine(
                line_type=SaleLineType.CATALOG,
                description=product.name,
                qty=line.qty,
                unit_price=disc.unit_price,
                line_total=disc.unit_price * line.qty,
                original_unit_price=disc.original_unit_price,
                discount_amount=disc.discount_per_unit * line.qty,
            )
        if line.line_type == SaleLineType.MENU:
            menu_item = await self._resolve_menu_item(store_id, line)
            return QuoteLine(
                line_type=SaleLineType.MENU,
                description=menu_item.name,
                qty=line.qty,
                unit_price=menu_item.unit_price,  # 餐飲不折活動，原價即成交價
                line_total=menu_item.unit_price * line.qty,
                original_unit_price=None,
                discount_amount=Decimal(0),
            )
        if line.bulk_lot_id is None:
            raise SaleLineInvalid("BULK_LOT 明細必須帶 bulk_lot_id")
        if line.qty <= 0:
            raise SaleLineInvalid("BULK_LOT 明細數量必須 > 0")
        lot = await self._inventory.get_bulk_lot(store_id, line.bulk_lot_id)
        if lot is None:
            raise SaleItemNotFound(f"找不到散裝批 {line.bulk_lot_id}")
        applies = _campaign_applies(
            campaign, line_type=SaleLineType.BULK_LOT, is_consignment=lot.consignor_id is not None
        )
        disc = _compute_discount(campaign, lot.unit_price, applies=applies)
        return QuoteLine(
            line_type=SaleLineType.BULK_LOT,
            description=lot.name,
            qty=line.qty,
            unit_price=disc.unit_price,
            line_total=disc.unit_price * line.qty,
            original_unit_price=disc.original_unit_price,
            discount_amount=disc.discount_per_unit * line.qty,
        )

    async def _process_line(
        self,
        store_id: int,
        sale_id: int,
        line: SaleLineInput,
        consignment_sales: list[tuple[int, Decimal, int]],
        campaign: Campaign | None,
    ) -> Decimal:
        """解析單行、原子扣庫存、寫 stock_movement(OUT)、建 sale_line；回傳該行含稅小計（折後）。"""
        if line.line_type == SaleLineType.SERIALIZED:
            return await self._process_serialized(
                store_id, sale_id, line, consignment_sales, campaign
            )
        if line.line_type == SaleLineType.CATALOG:
            return await self._process_catalog(store_id, sale_id, line, campaign)
        if line.line_type == SaleLineType.MENU:
            return await self._process_menu(store_id, sale_id, line)
        return await self._process_bulk(store_id, sale_id, line, campaign)

    async def _resolve_menu_item(self, store_id: int, line: SaleLineInput) -> MenuItem:
        """解析餐飲明細：驗 menu_item_id/qty、取本店未封存且可售的品項。"""
        if line.menu_item_id is None:
            raise SaleLineInvalid("MENU 明細必須帶 menu_item_id")
        if line.qty <= 0:
            raise SaleLineInvalid("MENU 明細數量必須 > 0")
        item = await self._menu.get(store_id, line.menu_item_id)
        if item is None or item.archived_at is not None:
            raise MenuItemNotFound(f"找不到餐飲品項 {line.menu_item_id}")
        if not item.is_available:
            raise MenuItemUnavailable(f"餐飲品項 {item.name} 目前停售")
        return item

    async def _process_menu(self, store_id: int, sale_id: int, line: SaleLineInput) -> Decimal:
        """餐飲明細：不扣庫存、不套活動折扣、原價成交；建 sale_line（line_type=MENU）。"""
        item = await self._resolve_menu_item(store_id, line)
        disc = _AppliedDiscount(item.unit_price, None, Decimal(0), None)
        await self._repo.add_line(
            SaleLine(
                store_id=store_id,
                sale_id=sale_id,
                line_type=SaleLineType.MENU,
                menu_item_id=item.id,
                description=item.name,
                qty=line.qty,
                **self._line_amounts(disc, qty=line.qty),
            )
        )
        return item.unit_price * line.qty

    async def _process_serialized(
        self,
        store_id: int,
        sale_id: int,
        line: SaleLineInput,
        consignment_sales: list[tuple[int, Decimal, int]],
        campaign: Campaign | None,
    ) -> Decimal:
        if line.item_code is None:
            raise SaleLineInvalid("SERIALIZED 明細必須帶 item_code")
        if line.qty != 1:
            raise SaleLineInvalid("SERIALIZED 明細數量必須為 1")
        item = await self._inventory.get_serialized_by_code(store_id, line.item_code)
        if item is None:
            raise SaleItemNotFound(f"找不到序號品 {line.item_code}")
        is_consignment = item.ownership_type == OwnershipType.CONSIGNMENT
        applies = _campaign_applies(
            campaign, line_type=SaleLineType.SERIALIZED, is_consignment=is_consignment
        )
        disc = _compute_discount(campaign, item.listed_price, applies=applies)  # qty 固定 1
        # 原子轉移 IN_STOCK→SOLD（已售出/併發競態 → 拋 InvalidStateTransition）。
        await self._inventory.sell_serialized_item(item.id)
        await self._inventory.record_stock_out(
            store_id,
            ItemKind.SERIALIZED,
            qty=1,
            reason=StockReason.SALE,
            ref_type="sale",
            ref_id=sale_id,
            serialized_item_id=item.id,
        )
        await self._repo.add_line(
            SaleLine(
                store_id=store_id,
                sale_id=sale_id,
                line_type=SaleLineType.SERIALIZED,
                serialized_item_id=item.id,
                description=item.name,
                qty=1,
                **self._line_amounts(disc, qty=1),
            )
        )
        if is_consignment:
            # 寄售品建檔時保證有 commission_pct（inventory 已驗），此處防呆。
            if item.commission_pct is None:
                raise SaleLineInvalid(f"寄售品 {line.item_code} 缺 commission_pct")
            # 寄售結算 gross＝實際成交（折後）價：有折扣時寄售人按折後分潤、無折扣即原價
            # （disc.unit_price 在未折時等於原 listed_price）。docs/21 §8.1：折扣一律按比例分攤。
            consignment_sales.append((item.id, disc.unit_price, item.commission_pct))
        return disc.unit_price

    @staticmethod
    def _line_amounts(disc: _AppliedDiscount, *, qty: int) -> dict[str, object]:
        """sale_line 的金額欄（折後實際成交＋折扣留痕）。"""
        return {
            "unit_price": disc.unit_price,
            "line_total": disc.unit_price * qty,
            "original_unit_price": disc.original_unit_price,
            "discount_amount": disc.discount_per_unit * qty,
            "campaign_id": disc.campaign_id,
        }

    async def _process_catalog(
        self, store_id: int, sale_id: int, line: SaleLineInput, campaign: Campaign | None
    ) -> Decimal:
        if line.catalog_product_id is None:
            raise SaleLineInvalid("CATALOG 明細必須帶 catalog_product_id")
        if line.qty <= 0:
            raise SaleLineInvalid("CATALOG 明細數量必須 > 0")
        product = await self._inventory.get_catalog(store_id, line.catalog_product_id)
        if product is None:
            raise SaleItemNotFound(f"找不到數量型商品 {line.catalog_product_id}")
        applies = _campaign_applies(campaign, line_type=SaleLineType.CATALOG, is_consignment=False)
        disc = _compute_discount(campaign, product.unit_price, applies=applies)
        await self._inventory.sell_catalog_items(product.id, line.qty)
        await self._inventory.record_stock_out(
            store_id,
            ItemKind.CATALOG,
            qty=line.qty,
            reason=StockReason.SALE,
            ref_type="sale",
            ref_id=sale_id,
            catalog_product_id=product.id,
        )
        await self._repo.add_line(
            SaleLine(
                store_id=store_id,
                sale_id=sale_id,
                line_type=SaleLineType.CATALOG,
                catalog_product_id=product.id,
                description=product.name,
                qty=line.qty,
                **self._line_amounts(disc, qty=line.qty),
            )
        )
        return disc.unit_price * line.qty

    async def _process_bulk(
        self, store_id: int, sale_id: int, line: SaleLineInput, campaign: Campaign | None
    ) -> Decimal:
        if line.bulk_lot_id is None:
            raise SaleLineInvalid("BULK_LOT 明細必須帶 bulk_lot_id")
        if line.qty <= 0:
            raise SaleLineInvalid("BULK_LOT 明細數量必須 > 0")
        lot = await self._inventory.get_bulk_lot(store_id, line.bulk_lot_id)
        if lot is None:
            raise SaleItemNotFound(f"找不到散裝批 {line.bulk_lot_id}")
        # 折扣只套自有散裝（applies_owned_bulk）；寄售散裝無抽成模型、不折（docs/21 §2）。
        applies = _campaign_applies(
            campaign, line_type=SaleLineType.BULK_LOT, is_consignment=lot.consignor_id is not None
        )
        disc = _compute_discount(campaign, lot.unit_price, applies=applies)
        # 原子扣減 remaining_qty（不足 → InsufficientStock；歸零自動轉 SOLD_OUT）。
        await self._inventory.sell_bulk_lot_items(lot.id, line.qty)
        await self._inventory.record_stock_out(
            store_id,
            ItemKind.BULK_LOT,
            qty=line.qty,
            reason=StockReason.SALE,
            ref_type="sale",
            ref_id=sale_id,
            bulk_lot_id=lot.id,
        )
        await self._repo.add_line(
            SaleLine(
                store_id=store_id,
                sale_id=sale_id,
                line_type=SaleLineType.BULK_LOT,
                bulk_lot_id=lot.id,
                description=lot.name,
                qty=line.qty,
                **self._line_amounts(disc, qty=line.qty),
            )
        )
        return disc.unit_price * line.qty
