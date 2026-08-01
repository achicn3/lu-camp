"""退貨時的發票處置政策（純決策邏輯，無 DB / 無 I/O，可完整單元測試）。

法規依據與店主裁示（2026-08-01，見 ADR-014）：
- 《統一發票使用辦法》第 20 條：退回時**銷售額尚未申報者**，非營業人買受人「應收回原開立
  統一發票收執聯…並註明作廢」；**已申報者**改取得折讓證明單。
- 系統無從得知「何時申報」，依店主裁示以「**發票開立日與退貨日是否同一曆月（台北時區）**」
  作為代理判準（Amego 亦以此為會計慣例：當月作廢、跨月折讓）。店主確認不會在當月提前申報。
- 部分退貨一律折讓——原發票對**未退商品仍然有效**，是客人的購買憑證，不得收回。

關鍵不變量：
1. **原發票只要已有任何折讓，後續一律繼續折讓，不得再作廢原發票**——否則會同時存在
   「已作廢的原發票」與「先前開出的折讓單」，帳目自相矛盾。
2. 只要原發票已有折讓（**已核可或在途**）→ 一律繼續折讓、不作廢。作廢在途或結果未知 →
   任何稅務動作都不得疊加，轉人工（REVIEW_REQUIRED）。
3. 需要收回紙本卻未收回時，**不執行 F0501**；是否連帶擋下退貨由呼叫端依店主政策決定
   （本店裁示：累計全退且需收回紙本而未收回 → 拒絕退貨）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

_TAIPEI_TZ = ZoneInfo("Asia/Taipei")


class ReturnInvoiceAction(StrEnum):
    """本次退貨要對原發票做什麼。"""

    NONE = "NONE"  # 無發票或發票尚未開立成功——不做稅務動作（既有 PENDING 路徑另處理）
    ALLOWANCE = "ALLOWANCE"  # 開立折讓（G0401）
    VOID = "VOID"  # 作廢原發票（F0501）
    REVIEW_REQUIRED = "REVIEW_REQUIRED"  # 狀態未收斂，不可自動決定，轉人工


@dataclass(frozen=True)
class InvoiceFacts:
    """決策所需的發票事實（由呼叫端自 DB 讀出後傳入，此模組不碰 DB）。"""

    exists: bool
    is_issued: bool
    """平台已核可、正式開立。"""
    issued_at: datetime | None
    """發票開立時間（UTC）；判定同月時換算為台北時區。"""
    has_settled_allowance: bool
    """已有**成功**的折讓紀錄。"""
    has_inflight_allowance: bool
    """有折讓在途或結果未知（尚未與平台收斂）。

    與 `has_settled_allowance` 同等對待：**只要有折讓（在途或已核可）就一律繼續折讓、
    不作廢原發票**。不擋下退貨本身——分次退貨會有多張 G0401 同時在途是正常操作，系統已能
    正確收斂（見 test_multiple_inflight_allowances_transition_only_when_all_accepted）；
    若在此擋下，等於因為前一張折讓還沒回執就拒絕客人退款。
    """

    has_inflight_void: bool
    """有作廢在途或結果未知——此時任何稅務動作都不得再疊加，一律轉人工。"""
    print_mark: bool
    """開立時列印了紙本證明聯。"""
    carrier_type: str | None
    donate_mark: bool


@dataclass(frozen=True)
class ReturnInvoiceDecision:
    action: ReturnInvoiceAction
    requires_paper_recall: bool
    """需向客人收回紙本證明聯才可作廢。"""
    requires_customer_consent: bool
    """需買受人同意（電子發票實施作業要點第 9 點）：折讓與作廢皆需。"""
    reason: str
    """給店員看的說明；也用於預覽 API 與稽核。"""


def has_paper_copy(facts: InvoiceFacts) -> bool:
    """是否存在需要收回的紙本證明聯。

    載具（carrier_type 有值）或捐贈（donate_mark）皆不列印紙本給客人，無從收回；
    僅 print_mark 為真且非載具非捐贈者才有紙本。
    """
    if not facts.print_mark:
        return False
    if facts.carrier_type is not None:
        return False
    return not facts.donate_mark


def same_taipei_month(issued_at: datetime, now: datetime) -> bool:
    """兩個時間點是否落在同一台北曆月（跨月即不同）。"""
    issued_local = issued_at.astimezone(_TAIPEI_TZ)
    now_local = now.astimezone(_TAIPEI_TZ)
    return (issued_local.year, issued_local.month) == (now_local.year, now_local.month)


def decide(
    facts: InvoiceFacts,
    *,
    is_full_return: bool,
    now: datetime,
) -> ReturnInvoiceDecision:
    """依原發票事實與本次退貨範圍，決定要折讓、作廢、或轉人工。

    `is_full_return`＝**本次退貨後累計**是否所有可退品項都退完（由呼叫端計算，含餐飲的
    混合單因餐飲不可退而永遠不成立）。
    """
    if not facts.exists or not facts.is_issued:
        return ReturnInvoiceDecision(
            action=ReturnInvoiceAction.NONE,
            requires_paper_recall=False,
            requires_customer_consent=False,
            reason="原交易沒有已開立的發票，本次退貨不涉及發票處置。",
        )

    consent = True  # 折讓與作廢皆須買受人同意
    if facts.has_inflight_void:
        return ReturnInvoiceDecision(
            action=ReturnInvoiceAction.REVIEW_REQUIRED,
            requires_paper_recall=False,
            requires_customer_consent=consent,
            reason="原發票的作廢尚在處理中（結果未確認），不可再疊加稅務動作，請轉人工處理。",
        )
    if facts.has_settled_allowance or facts.has_inflight_allowance:
        # 只要這張發票**已經有折讓**（已核可或在途），後續退貨一律繼續折讓、絕不作廢原發票
        # ——否則會同時存在「已作廢的原發票」與折讓單。在途也算數：那張折讓可能隨時核可。
        return ReturnInvoiceDecision(
            action=ReturnInvoiceAction.ALLOWANCE,
            requires_paper_recall=False,
            requires_customer_consent=consent,
            reason="原發票已開過折讓，後續退貨一律繼續開折讓（不可再作廢原發票）。",
        )
    if not is_full_return:
        return ReturnInvoiceDecision(
            action=ReturnInvoiceAction.ALLOWANCE,
            requires_paper_recall=False,
            requires_customer_consent=consent,
            reason="部分退貨：原發票對未退商品仍有效，開立折讓單。",
        )
    if facts.issued_at is None or not same_taipei_month(facts.issued_at, now):
        return ReturnInvoiceDecision(
            action=ReturnInvoiceAction.ALLOWANCE,
            requires_paper_recall=False,
            requires_customer_consent=consent,
            reason="整筆退貨，但已跨月（原發票非本月開立），依規定改開折讓單。",
        )
    paper = has_paper_copy(facts)
    return ReturnInvoiceDecision(
        action=ReturnInvoiceAction.VOID,
        requires_paper_recall=paper,
        requires_customer_consent=consent,
        reason=(
            "整筆退貨且原發票為本月開立：作廢原發票。需先向客人收回紙本證明聯。"
            if paper
            else "整筆退貨且原發票為本月開立：作廢原發票（客人使用載具或捐贈，無紙本須收回）。"
        ),
    )
