"""einvoice 業務邏輯：本地發票紀錄 + Turnkey 外送佇列狀態機（唯一協調層）。

狀態語意（誠實反映實際）：
- 結帳建立的發票為 **PENDING**（本地已建、排入上傳佇列，尚無平台核可字軌號碼），
  對應 sale.invoice_status=PENDING_ISSUE——**非「已開立」**。字軌配號/XML 序列化待 T13 收尾。
- 唯有平台 **ProcessResult 成功** 才把發票轉 ISSUED、佇列轉 UPLOADED。SummaryResult 只作
  批次對帳、不代表單筆核可，故不改單筆狀態（docs/18 §7.3）。

不變量（測試 + DB 約束守護）：
1. 一筆銷售至多一張發票——`create_pending_invoice` 冪等（重入回原發票、不重建、不重排隊）。
2. 重送（retry）只把 FAILED 轉回 PENDING+attempts+1，絕不新建發票或新配字軌號碼。
3. 拋檔為原子檔案交付、且僅對 PENDING 且未拋檔、對應發票未作廢者為之（不重複/無效上傳）。
4. 折讓只對已開立（ISSUED）發票、累計不超過原發票、同退貨至多一張（§7 不變量 5）。
"""

import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import ClassVar, NamedTuple
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.config import get_settings as get_app_settings
from app.core.money import split_tax_inclusive
from app.modules.einvoice.amego import (
    AMEGO_PRINTER_TYPE_TM_T82III,
    AmegoClient,
    AmegoIssueResult,
    HttpxAmegoTransport,
    allowance_number,
    amego_order_id,
    build_allowance_query_data,
    build_f0401_data,
    build_f0501_data,
    build_g0401_data,
    build_invoice_print_data,
    build_invoice_query_by_number_data,
    build_invoice_query_data,
    parse_f0401_success,
    parse_invoice_print,
    parse_query_allowance_exists,
    parse_query_invoice_voided,
    parse_query_issued,
)
from app.modules.einvoice.dropper import EInvoiceDropper
from app.modules.einvoice.models import (
    EInvoiceResultEvent,
    EInvoiceUploadQueue,
    Invoice,
    InvoiceAllowance,
)
from app.modules.einvoice.repository import EInvoiceRepository
from app.modules.einvoice.serializer import InvoiceXmlSerializer
from app.modules.store.service import StoreService
from app.shared.enums import (
    EInvoiceAction,
    EInvoiceIssueChannel,
    EInvoiceMessageType,
    InvoiceStatus,
    InvoiceType,
    InvoiceVoidReason,
    UploadStatus,
)
from app.shared.exceptions import (
    AllowanceExceedsInvoice,
    AmegoIssueFailed,
    AmegoTransportError,
    DuplicateAllowanceForReturn,
    EInvoiceDropError,
    EInvoiceQueueItemNotFound,
    EInvoiceQueueNotDroppable,
    EInvoiceQueueNotRetryable,
    EInvoiceResultConflict,
    EInvoiceResultNotApplicable,
    InvoiceIncompleteForIssue,
    InvoiceNotFound,
    InvoiceNotIssued,
    ManualInvoiceNotRegisterable,
    ManualPaperInvoiceOperation,
)

# 回執種類（einvoice_result_events.result_kind）。
RESULT_KIND_PROCESS = "PROCESS"
RESULT_KIND_SUMMARY = "SUMMARY"

# 發票開立日以台灣時區呈現（Amego invoice_time 為 Unix 秒；折讓日亦同）。
_TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def _may_have_reached_platform(item: EInvoiceUploadQueue) -> bool:
    """這一列的訊息**是否可能已經送到平台**（docs/36）。

    `xml_path`/`dropped_at` 不足以判斷：Amego 路徑在認領（凍結 payload）時就寫入這兩欄
    並 commit，**先於**對帳查詢與實際 POST。若查詢因斷網失敗，F0401 其實從未送出——
    以痕跡判定會在平台/網路故障時把手開登記鎖死，而那正是本功能存在的理由。

    真正的證據有兩種：
    - `posted_at`：Amego 送出端點呼叫前寫入。
    - `attempts > 0`：只有 `retry()` 會加，而 retry 僅適用 FAILED，代表平台答覆過。

    舊 Turnkey outbox 的列不在此判斷——那條路正式環境走不到（`drop_pending` 已無呼叫端、
    序列化器是刻意拋錯的樁），真的出現就是狀態不明，由呼叫端 fail closed 轉人工，
    不在這裡模擬一條死路的語意。
    """
    return item.posted_at is not None or item.attempts > 0


def _payload_first(payload: object, *, ctx: str) -> dict[str, object]:
    """取凍結 payload 的**唯一**一筆（Amego 各訊息皆為陣列，本系統一次只送一筆）。

    多筆一律拒絕：只驗第一筆卻把整個陣列送出去，等於對未驗證的其餘筆放行。
    """
    if isinstance(payload, list):
        if len(payload) != 1 or not isinstance(payload[0], dict):
            raise EInvoiceDropError(
                f"{ctx}：凍結 payload 應恰含一筆訊息，實得 {len(payload)} 筆（需人工對帳）"
            )
        return payload[0]
    if isinstance(payload, dict):
        return payload
    raise EInvoiceDropError(f"{ctx}：凍結 payload 形狀不明，拒絕上送（需人工對帳）")


def _assert_payload_targets(payload: object, field: str, expected: str, *, ctx: str) -> None:
    """凍結 payload 的**外部識別碼**必須等於本佇列列的目標。

    對帳查的是本地推導出來的識別碼，真正 POST 出去的卻是 frozen payload。兩者若不一致
    （舊版／還原後的 payload、或自洽 checksum 的損壞內容），就會「查本筆得到查無 → 送出
    別筆 → 把本列標成功」，且 crash 重試時仍查錯對象、永遠無法可靠對帳（Codex 第六輪）。
    """
    head = _payload_first(payload, ctx=ctx)
    actual = str(head.get(field) or "")
    if actual != expected:
        raise EInvoiceDropError(
            f"{ctx}：凍結 payload 的 {field}「{actual}」與本佇列目標「{expected}」不符"
            "，拒絕上送（需人工對帳）"
        )


def _payload_int(data: dict[str, object], field: str, *, ctx: str) -> Decimal:
    """取凍結 payload 的整數元金額欄。bool 是 int 子類，不得矇混。"""
    raw = data.get(field)
    if type(raw) is int:
        return Decimal(raw)
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        return Decimal(raw.strip())
    raise EInvoiceDropError(f"{ctx}：凍結 payload 缺 {field}，無從驗證身分（需人工對帳）")


def _payload_total(payload: object) -> Decimal:
    """F0401 對帳基準＝**我們實際送出**的 TotalAmount（非當下 DB 值，避免事後漂移）。"""
    return _payload_int(_payload_first(payload, ctx="f0401"), "TotalAmount", ctx="f0401")


def _payload_allowance_identity(payload: object) -> tuple[Decimal, Decimal, str]:
    """G0401 對帳基準：送出的未稅/稅額與原發票字軌（皆取自凍結 payload）。"""
    head = _payload_first(payload, ctx="g0401")
    net = _payload_int(head, "TotalAmount", ctx="g0401")  # g0401 的 TotalAmount 為未稅
    tax = _payload_int(head, "TaxAmount", ctx="g0401")
    items = head.get("ProductItem")
    original = ""
    if isinstance(items, list) and items and isinstance(items[0], dict):
        original = str(items[0].get("OriginalInvoiceNumber") or "")
    if not original:
        raise EInvoiceDropError("g0401：凍結 payload 缺原發票字軌，無從驗證身分（需人工對帳）")
    return net, tax, original


class ProofPrintPayload(NamedTuple):
    """證明聯列印內容＋這張先前印過沒有。

    `is_reprint` 由後端依「印出來過沒有」決定並回給前端顯示，不讓 UI 自己猜。
    **紙上一律不加註「補印」**（店主 2026-08-29 裁示，見 `reprint_payload_for_sale`）；
    這個旗標只用來提醒店員「這張之前印過了」，不改變印出來的版面。
    """

    base64_data: str
    is_reprint: bool


_AUTO_SEND_ACTIONS = (EInvoiceAction.VOID, EInvoiceAction.ALLOWANCE)
"""會被背景自動送出的動作——「卡住」只對這兩種成立（開立不自動送，見 background_service）。"""


async def build_amego_client(session: AsyncSession, store_id: int) -> AmegoClient:
    """組 Amego 客戶端：賣方統編＝stores.tax_id、App Key＝環境變數（docs/24）。

    HTTP 路由與背景送出**共用同一份**——兩邊各組一次遲早會在憑證來源或 base_url 上漂移。
    """
    cfg = get_app_settings()
    store = await StoreService(session).get_receipt_header(store_id)
    return AmegoClient(
        seller_tax_id=store.tax_id or "",
        app_key=cfg.amego_app_key,
        transport=HttpxAmegoTransport(),
        base_url=cfg.amego_api_base,
    )


class EInvoiceService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = EInvoiceRepository(session)

    async def create_pending_invoice(
        self,
        store_id: int,
        *,
        sale_id: int,
        total: Decimal,
        tax_rate: Decimal,
        invoice_type: InvoiceType = InvoiceType.B2C,
        buyer_tax_id: str | None = None,
        buyer_name: str | None = None,
        carrier_type: str | None = None,
        carrier_id: str | None = None,
        donate_mark: bool = False,
        npoban: str | None = None,
        print_mark: bool = True,
    ) -> Invoice:
        """建立**待開立（PENDING）**發票並排入 F0401 上傳佇列（冪等：同一 sale 重入回原發票）。

        非「已開立」：字軌號碼/開立日/隨機碼於 T13 收尾（配號 + XSD 序列化）與平台核可後才有，
        屆時經 record_result(PROCESS, success) 轉 ISSUED。稅於總額層級推算一次（§6）：
        net = round_ntd(total/(1+rate))、tax = total − net，保證 net + tax = total（不差一元）。
        """
        existing = await self._repo.find_invoice_by_sale(store_id, sale_id)
        if existing is not None:
            return existing

        # B2C 買方統編恆為空（序列化時填制式 "0000000000"，docs/14 §2）。
        if invoice_type is InvoiceType.B2C:
            buyer_tax_id = None

        net, tax = split_tax_inclusive(total, tax_rate)
        invoice = Invoice(
            store_id=store_id,
            sale_id=sale_id,
            tax_rate=Decimal(tax_rate),
            invoice_type=invoice_type,
            buyer_tax_id=buyer_tax_id,
            buyer_name=buyer_name,
            carrier_type=carrier_type,
            carrier_id=carrier_id,
            donate_mark=donate_mark,
            npoban=npoban,
            print_mark=print_mark,
            net=Decimal(net),
            tax=Decimal(tax),
            total=Decimal(net + tax),
            status=InvoiceStatus.PENDING,
        )
        await self._repo.add_invoice(invoice)
        await self._repo.add_queue_item(
            EInvoiceUploadQueue(
                store_id=store_id,
                action=EInvoiceAction.ISSUE,
                message_type=EInvoiceMessageType.F0401,
                invoice_id=invoice.id,
                status=UploadStatus.PENDING,
            )
        )
        return invoice

    async def register_manual_invoice(
        self,
        store_id: int,
        sale_id: int,
        *,
        invoice_no: str,
        invoice_date: date,
        invoice_time: str | None,
        random_number: str | None,
        total: Decimal,
        note: str | None,
        actor_user_id: int | None,
        client_factory: Callable[[], Awaitable[AmegoClient]],
    ) -> Invoice:
        """登記手開紙本備用發票（docs/36）：字軌用完/平台故障時當場開給客人的那一張。

        **重點在最後一步**：把該發票待送的 F0401 佇列列轉 CANCELLED。不做的話，字軌恢復
        後任何人按「重試開立」，平台就真的會再開一張——同一筆交易兩張發票，這是稅務事故。

        鎖序沿用全域約定 sale → queue（見 `issue_for_sale`；反序會與作廢/退貨路徑 AB-BA
        死鎖）。發票必須仍是 PENDING；金額必須與既有發票相符（登記不是改金額的後門）。
        號碼重複由部分唯一索引 `uq_invoices_store_invoice_no` 擋下，不另外查。
        """
        from app.modules.sales.service import SalesService  # 函式內 import 破循環

        invoice = await self._repo.find_invoice_by_sale(store_id, sale_id)
        if invoice is None:
            raise InvoiceNotFound(f"銷售 {sale_id} 無發票（einvoice 未啟用或非本店）")
        await SalesService(self._session).lock_sale_row(store_id, sale_id)
        await self._session.refresh(invoice)
        if invoice.status is not InvoiceStatus.PENDING:
            raise ManualInvoiceNotRegisterable(
                f"發票狀態 {invoice.status.value}，不可登記手開紙本（僅待開立的發票可登記）"
            )
        if Decimal(total) != Decimal(invoice.total):
            raise ManualInvoiceNotRegisterable(
                f"登記金額 {total} 與本筆發票金額 {invoice.total} 不符；"
                "登記手開發票不會更動金額，請確認紙本內容"
            )

        invoice.invoice_no = invoice_no
        invoice.invoice_date = invoice_date
        invoice.invoice_time = invoice_time
        invoice.random_number = random_number
        invoice.issue_channel = EInvoiceIssueChannel.MANUAL_PAPER
        invoice.status = InvoiceStatus.ISSUED
        # barcode/QR 一律留空：那是平台回傳的證明聯內容，紙本沒有也不該印（前端據此擋下）。

        issue_items = [
            item
            for item in await self._repo.lock_queue_items_for_invoice(store_id, invoice.id)
            if item.action is EInvoiceAction.ISSUE
        ]
        # **只要曾經送出過，就必須先問平台**（Codex 對抗審查第三輪 critical）。
        # 原本只擋「PENDING 且已認領」而放行 FAILED，理由是「FAILED＝平台明確拒絕」——
        # 這條規則不成立：`send_via_amego` 是 `success = code == 0`，**任何非零碼都變 FAILED**，
        # 其中包含「訂單號碼已存在」這類**代表平台其實已經有這張發票**的錯誤。
        # 因此不論 PENDING 或 FAILED，只要 xml_path/dropped_at 有值（曾認領/曝光），
        # 就以 invoice_query 向平台求證；沿用既有的「對帳先行」設計與其三態解析：
        #   平台有 → 拒絕登記（否則紙本＋電子兩張）
        #   明確查無（code=71） → 放行
        #   其餘（含連不上/授權失敗/回應曖昧） → parse_query_issued 拋錯，fail closed
        # 待開立發票必須看得到它的 ISSUE 佇列列；看不到就無從判斷是否曾送出 → fail closed。
        if not issue_items:
            raise ManualInvoiceNotRegisterable(
                "找不到本筆發票的開立佇列紀錄，無法確認是否曾送出平台；請人工對帳後再處理。"
            )
        # 舊 Turnkey outbox 認領（非 amego: 前綴）：檔案可能已落到 SRC 被撿走，而 Amego
        # 查詢對它毫無意義 → 一律轉人工，不由系統自行判斷（同 send_via_amego 的既有口徑）。
        if any(
            item.xml_path is not None and not item.xml_path.startswith("amego:")
            for item in issue_items
        ):
            raise ManualInvoiceNotRegisterable(
                "本筆的開立訊息由非 Amego 路徑認領（舊 Turnkey outbox），"
                "無法自動確認平台狀態；請人工對帳後再處理。"
            )
        if any(_may_have_reached_platform(item) for item in issue_items):
            # **延遲取得客戶端**：從未送出過的單（例如電子發票剛啟用就故障）不必被憑證卡住。
            client = await client_factory()
            order_id = amego_order_id(store_id=store_id, sale_id=sale_id)
            query_resp = await client.call(
                "/json/invoice_query", build_invoice_query_data(order_id=order_id)
            )
            existing_on_platform = parse_query_issued(
                query_resp,
                expect_total=Decimal(invoice.total),
                expect_not_before=invoice.created_at,
            )
            if existing_on_platform is not None:
                raise ManualInvoiceNotRegisterable(
                    f"平台上已經有這筆交易的發票（{existing_on_platform.invoice_no}），"
                    "不可再登記手開紙本，否則同一筆交易會有兩張發票。"
                    "請改以「重試開立」讓系統補記平台的開立結果。"
                )

        cancelled = 0
        for item in issue_items:
            if item.status in (UploadStatus.PENDING, UploadStatus.FAILED):
                item.status = UploadStatus.CANCELLED
                cancelled += 1

        await SalesService(self._session).mark_invoice_issued(store_id, sale_id)
        # 人工輸入稅務號碼屬敏感操作（CLAUDE.md §5）：誰、何時、對象、前後值都要留。
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="REGISTER_MANUAL_INVOICE",
            entity_type="invoice",
            entity_id=str(invoice.id),
            before={"status": InvoiceStatus.PENDING.value, "issue_channel": "AMEGO"},
            after={
                "status": invoice.status.value,
                "issue_channel": invoice.issue_channel.value,
                "invoice_no": invoice_no,
                "invoice_date": invoice_date.isoformat(),
                "sale_id": sale_id,
                "cancelled_queue_items": cancelled,
                "note": note,
            },
            is_sensitive=True,
        )
        return invoice

    async def _enqueue_f0501(self, store_id: int, invoice_id: int) -> None:
        """排入 F0501（作廢）上傳佇列（作廢已核可發票；裁示：作廢走 F0501）。"""
        await self._repo.add_queue_item(
            EInvoiceUploadQueue(
                store_id=store_id,
                action=EInvoiceAction.VOID,
                message_type=EInvoiceMessageType.F0501,
                invoice_id=invoice_id,
                status=UploadStatus.PENDING,
            )
        )

    async def issue_channels_for_sales(
        self, store_id: int, sale_ids: list[int]
    ) -> dict[int, EInvoiceIssueChannel]:
        """一批銷售各自的發票開立來源（docs/36；跨模組供 sales 列表用，§2 經 service）。

        沒有發票的銷售不會出現在結果裡。交易紀錄要據此**在顯示任何退款指示之前**就知道
        「這筆是手開紙本」——否則店員會先被叫去退款，之後才被後端擋下（錢已經出去了）。
        """
        if not sale_ids:
            return {}
        return await self._repo.issue_channels_for_sales(store_id, sale_ids)

    async def list_registerable_sale_ids(
        self, store_id: int, *, limit: int, offset: int
    ) -> list[int]:
        """可登記手開發票的銷售 id（發票仍為 PENDING），**不限日期**。

        原本前端只查今日、最多 200 筆再於客端過濾：昨天沒收斂的單看不到，
        而規格說這是唯一的救援途徑。資格也改由後端以**實際發票狀態**判定——
        客端從 sale.invoice_status 推導會把「電子發票關閉、根本沒有發票」的單也列進來，
        按下去只會 404。
        """
        return await self._repo.list_pending_invoice_sale_ids(store_id, limit=limit, offset=offset)

    async def assert_platform_voidable(
        self, store_id: int, sale_id: int, *, manual_paper_disposed: bool = False
    ) -> None:
        """作廢前置檢查（docs/36）：紙本發票不可走平台作廢——**必須在任何副作用之前呼叫**。

        `void_invoice_for_sale` 裡也有同一道守衛（防直接呼叫），但那裡已經在現金/購物金
        反轉與**不可逆的 LINE Pay 退款**之後：在那裡才擋，主交易雖回滾，已送出的外部退款
        收不回來 → 客人拿到錢、單子還有效、發票也沒作廢（Codex 對抗審查 critical #2）。
        純讀、無副作用，安全地放在最前面。
        """
        invoice = await self._repo.find_invoice_by_sale(store_id, sale_id)
        if (
            invoice is not None
            and invoice.issue_channel is EInvoiceIssueChannel.MANUAL_PAPER
            and not manual_paper_disposed
        ):
            raise ManualPaperInvoiceOperation(
                "本筆為手開紙本發票，系統不代管作廢；"
                "請依國稅局程序作廢紙本並保留收回聯後，"
                "再以「紙本已作廢」確認完成本筆銷售的作廢。"
            )

    async def void_invoice_for_sale(
        self,
        store_id: int,
        sale_id: int,
        *,
        reason: InvoiceVoidReason = InvoiceVoidReason.SALE_VOID,
        actor_user_id: int | None,
        manual_paper_disposed: bool = False,
    ) -> Invoice | None:
        """銷售作廢時中止其電子發票（由 sales.void_sale 呼叫；跨模組經 service，§2）。

        依「平台是否可能已收到開立」決定，避免把已交付 Turnkey 的發票當成平台沒收過：
        - **ISSUED**（平台已核可）：標 VOID_PENDING 並排 F0501（作廢），待 F0501 核可才轉正式 VOID。
        - **PENDING 且 F0401 已拋檔**（dropped_at≠None、待回執）：平台可能仍會開立 → 標
          VOID_PENDING、**不取消 F0401**；由 record_result 決定：F0401 成功→自動排 F0501 續作廢、
          F0401 失敗→轉 VOID。
        - **PENDING 且 F0401 未拋檔**：平台從未收過 → 直接 VOID，待送 F0401 標 CANCELLED。
        - 已 VOID_PENDING / VOID → 冪等。無發票（einvoice 關閉）→ no-op。
        """
        invoice = await self._repo.find_invoice_by_sale(store_id, sale_id)
        if invoice is None:
            return None
        if invoice.issue_channel is EInvoiceIssueChannel.MANUAL_PAPER:
            if not manual_paper_disposed:
                # 手開紙本（docs/36）：平台上沒有這張發票，F0501 沒有對象可作廢。
                raise ManualPaperInvoiceOperation(
                    "本筆為手開紙本發票，系統不代管作廢；"
                    "請依國稅局程序作廢紙本並保留收回聯後，"
                    "再以「紙本已作廢」確認完成本筆銷售的作廢。"
                )
            # 店長已確認紙本作廢完成：**只做本地作廢**（發票 VOID、銷售反轉），
            # 絕不排 F0501——平台上沒有這張發票可作廢。
            if invoice.status in (InvoiceStatus.VOID, InvoiceStatus.VOID_PENDING):
                return invoice
            status_before_paper = invoice.status
            invoice.status = InvoiceStatus.VOID
            invoice.void_reason = reason
            await self._audit_invoice_void_transition(
                store_id,
                invoice,
                status_before=status_before_paper,
                reason=reason,
                actor_user_id=actor_user_id,
                source="manual_paper_disposed",
            )
            return invoice
        if invoice.status in (InvoiceStatus.VOID, InvoiceStatus.VOID_PENDING):
            return invoice
        status_before = invoice.status
        # 作廢原因（SALE_VOID／FULL_RETURN／CORRECTION）：同樣是「作廢」，帳務意義不同，
        # 必須可分辨——報表與稽核據此區分「這筆交易不算數」與「交易有效但全退」。
        # **與狀態同時設定**：ck_invoices_void_reason_matches_status 要求兩者一致，中途若被
        # autoflush 寫出（下方查詢會觸發）就會違反約束。
        if invoice.status is InvoiceStatus.ISSUED:
            invoice.status = InvoiceStatus.VOID_PENDING
            invoice.void_reason = reason
            await self._enqueue_f0501(store_id, invoice.id)
        else:  # PENDING（尚未平台核可）
            # FOR UPDATE：與交付協議同鎖（Codex 第五輪）——避免讀到過期未認領列、
            # 在另一 worker 曝光檔案後才取消（交付持列鎖期間，本查詢會等待其 commit）。
            issue_items = [
                i
                for i in await self._repo.lock_queue_items_for_invoice(store_id, invoice.id)
                if i.action is EInvoiceAction.ISSUE and i.status is UploadStatus.PENDING
            ]
            # 「已認領」（xml_path 設）即視為在途：認領後檔案就可能已曝光給 Turnkey
            # （兩階段拋檔的 crash 窗口），不可當平台沒收過而 CANCELLED。已認領未確認的列
            # 允許在 VOID_PENDING 下恢復完成交付（見 _serialize），回執到來後由
            # 「F0401 成功→續 F0501／失敗→VOID」收斂，不會卡死。
            in_flight = any(i.xml_path is not None for i in issue_items)
            if in_flight:
                # 已交付 Turnkey、平台結果未回：不可視為沒收過，改請求作廢、留 F0401 待回執決定。
                invoice.status = InvoiceStatus.VOID_PENDING
            else:
                invoice.status = InvoiceStatus.VOID
                for item in issue_items:
                    item.status = UploadStatus.CANCELLED
            invoice.void_reason = reason
        # 作廢發票屬敏感操作，須留下「誰、何時、對象、前後值」（CLAUDE.md §5）。
        # 銷售層的 VOID_SALE／CREATE_RETURN 稽核記的是交易，追不出哪張發票由什麼狀態變成什麼。
        await self._audit_invoice_void_transition(
            store_id,
            invoice,
            status_before=status_before,
            source="STAFF",
            actor_user_id=actor_user_id,
            reason=reason,
        )
        await self._session.flush()
        return invoice

    async def _audit_invoice_void_transition(
        self,
        store_id: int,
        invoice: Invoice,
        *,
        status_before: InvoiceStatus,
        source: str,
        actor_user_id: int | None = None,
        reason: InvoiceVoidReason | None = None,
    ) -> None:
        """作廢流程的每一次狀態轉移都留一筆 invoice 級稽核（CLAUDE.md §5）。

        `source` 明確標示這一步是誰推動的：`STAFF`（店員發起作廢）或平台回執
        （`F0501_ACCEPTED`／`F0401_FAILED`）。平台回執沒有操作者是事實，不是遺漏——
        以 source 說清楚，比留一個空白的「誰」更誠實（Codex 第三輪 #3）。
        """
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="VOID_INVOICE",
            entity_type="invoice",
            entity_id=str(invoice.id),
            before={"status": status_before.value},
            after={
                "status": invoice.status.value,
                "void_reason": (reason or invoice.void_reason or "").__str__(),
                "sale_id": invoice.sale_id,
                "invoice_no": invoice.invoice_no,
                "source": source,
            },
            is_sensitive=True,
        )

    async def record_allowance(
        self,
        store_id: int,
        *,
        invoice_id: int,
        total: Decimal,
        return_id: int | None = None,
    ) -> InvoiceAllowance:
        """開立折讓單並排入 G0401 上傳佇列（退貨且原發票已開立，§7 不變量 5）。

        守衛（F6）：原發票必須已開立（ISSUED，否則 InvoiceNotIssued）；同一退貨至多一張折讓
        （return_id 唯一，否則 DuplicateAllowanceForReturn）；累計折讓不得超過原發票總額
        （否則 AllowanceExceedsInvoice）。

        稅拆分**一律用原發票的稅率快照**（invoice.tax_rate；Codex 第十輪）：呼叫端不得
        注入活 settings 稅率——結帳後改稅率，折讓的銷項稅沖回必須仍與原發票同口徑。
        """
        # 同一發票的折讓以發票列為序列化錨點：兩筆並發部分退貨不可同時讀到相同累計值，
        # 否則可能超額折讓，也無法正確分配最後一筆稅額尾差。
        invoice = await self._repo.get_invoice(store_id, invoice_id, for_update=True)
        if invoice is None:
            raise InvoiceNotFound(f"發票不存在或不屬於本店：id={invoice_id}")
        if invoice.issue_channel is EInvoiceIssueChannel.MANUAL_PAPER:
            # 手開紙本（docs/36）：平台上沒有這張發票，G0401 會指向不存在的原發票。
            raise ManualPaperInvoiceOperation(
                "本筆為手開紙本發票，系統不代管折讓；請依國稅局程序開立紙本折讓證明單並留存。"
            )
        if invoice.status is not InvoiceStatus.ISSUED:
            raise InvoiceNotIssued(
                f"發票 {invoice_id} 尚未開立（狀態 {invoice.status.value}），不可折讓"
            )
        if return_id is not None:
            existing = await self._repo.find_allowance_by_return(store_id, return_id)
            if existing is not None:
                raise DuplicateAllowanceForReturn(f"退貨單 {return_id} 已有折讓，不可重複開立")

        prior_net, prior_tax, prior_total = await self._repo.sum_allowances_amounts(
            store_id,
            invoice_id,
        )
        cumulative_total = prior_total + total
        if cumulative_total > invoice.total:
            raise AllowanceExceedsInvoice(
                f"折讓累計 {cumulative_total} 超過原發票總額 {invoice.total}"
            )

        target_cumulative_net: Decimal
        if cumulative_total == invoice.total:
            target_cumulative_net = Decimal(invoice.net)
        else:
            target_net_int, _target_tax_int = split_tax_inclusive(
                cumulative_total,
                Decimal(invoice.tax_rate),
            )
            target_cumulative_net = Decimal(target_net_int)
        preferred_net = target_cumulative_net - prior_net
        # 相容升級前已逐筆拆稅的發票：把本次未稅額夾在「剩餘未稅/稅額」容許區間內。
        # 如此每張折讓仍為非負整數，累計全退時一定精確等於原發票 net/tax。
        remaining_net = Decimal(invoice.net) - prior_net
        remaining_tax = Decimal(invoice.tax) - prior_tax
        min_net = max(Decimal(0), total - remaining_tax)
        max_net = min(total, remaining_net)
        net = min(max(preferred_net, min_net), max_net)
        tax = total - net
        allowance = InvoiceAllowance(
            store_id=store_id,
            invoice_id=invoice_id,
            return_id=return_id,
            net=Decimal(net),
            tax=Decimal(tax),
            total=Decimal(net + tax),
        )
        await self._repo.add_allowance(allowance)
        await self._repo.add_queue_item(
            EInvoiceUploadQueue(
                store_id=store_id,
                action=EInvoiceAction.ALLOWANCE,
                message_type=EInvoiceMessageType.G0401,
                allowance_id=allowance.id,
                status=UploadStatus.PENDING,
            )
        )
        return allowance

    async def drop_pending(
        self,
        store_id: int,
        queue_id: int,
        *,
        serializer: InvoiceXmlSerializer,
        dropper: EInvoiceDropper,
    ) -> EInvoiceUploadQueue:
        """把待送佇列列的 MIG XML 原子落入 Turnkey SRC 目錄（**兩階段、rollback-safe**）。

        檔案一落入 SRC 就可能被 Turnkey 撿走上傳——外部副作用**不可**發生在未 commit 的 DB
        交易內（Codex adversarial：crash 後 DB 說「沒拋過」、平台卻收到檔）。故本方法自管
        交易邊界（outbox 交付入口，偏離「呼叫端 commit」慣例、僅此一處）：

        1. **認領（先持久化）**：序列化（純函式）→ 寫入 xml_path/xml_sha256 → `commit`。
           此後即使 crash，DB 都記得「這筆已認領、內容 sha 已定」。
        2. 寫檔（原子、確定性檔名——重跑為覆寫同名檔，永不產生第二份）。
        3. **確認**：寫入 dropped_at → `commit`。

        Crash 恢復：重呼本方法——已認領未確認（xml_path 設、dropped_at NULL）→ 重新序列化並
        驗 sha 與認領一致（序列化必須確定性；不符即拒，防止內容漂移下重拋不同檔），覆寫檔案、
        補確認。已確認（dropped_at 設）→ 冪等 no-op。回執側以「已認領」即接受
        （見 record_result）——認領後檔案就可能已曝光。

        **交付世代**（Codex 第二輪）：檔名嵌入 `attempts` 世代（`…-a{n}.xml`）——每次 retry
        是新檔名新訊息，回執可歸屬世代；確認階段為 **compare-and-set**（認領 commit 後鎖已
        釋放，中間可能發生「失敗回執→retry 清認領」，恢復的確認絕不可污染已重試的列）。

        非 PENDING 或對應發票已作廢 → EInvoiceQueueNotDroppable；序列化未就緒 →
        EInvoiceSerializerNotReady（發生在認領前，無任何持久/檔案副作用）。
        """
        item = await self._repo.lock_queue_item(store_id, queue_id)
        if item is None:
            raise EInvoiceQueueItemNotFound(f"佇列項目不存在或不屬於本店：id={queue_id}")
        if item.status is not UploadStatus.PENDING:
            raise EInvoiceQueueNotDroppable(
                "這筆不是『等待送出』的狀態，不能再送一次，請重新整理頁面看最新狀態"
            )
        if item.dropped_at is not None:
            return item  # 已拋檔確認、待 Turnkey 上傳：冪等 no-op，不重複寫檔

        payload = await self._serialize(store_id, item, serializer)
        claim_attempts = item.attempts  # 交付世代快照（CAS 用）
        filename = f"{item.message_type.value}-{store_id}-{queue_id}-a{claim_attempts}.xml"
        expected_path = str(dropper.src_dir(item.message_type) / filename)
        claim_sha = hashlib.sha256(payload).hexdigest()

        if item.xml_path is None:
            # 階段 1：認領先於檔案曝光——commit 後才允許任何檔案副作用。
            item.xml_path = expected_path
            item.xml_sha256 = claim_sha
            await self._session.commit()
        elif item.xml_path != expected_path or item.xml_sha256 != claim_sha:
            # 恢復路徑：重算內容/世代路徑必須與認領一致（確定性序列化守衛）。
            raise EInvoiceDropError(
                f"佇列 {queue_id} 重拋內容與認領不符（序列化非確定性或目錄變更），"
                "拒絕覆寫已可能曝光的檔案"
            )

        # 階段 2：持列鎖「驗認領 → 寫檔 → 確認」單一交易（Codex 第四輪）。
        return await self._expose_and_confirm(
            store_id,
            queue_id,
            filename=filename,
            payload=payload,
            dropper=dropper,
            expected_path=expected_path,
            expected_sha=claim_sha,
            expected_attempts=claim_attempts,
        )

    async def _expose_and_confirm(
        self,
        store_id: int,
        queue_id: int,
        *,
        filename: str,
        payload: bytes,
        dropper: EInvoiceDropper,
        expected_path: str,
        expected_sha: str,
        expected_attempts: int,
    ) -> EInvoiceUploadQueue:
        """持列鎖完成「CAS 驗認領 → 寫檔曝光 → 確認 dropped_at」（單一交易）。

        認領 commit 後鎖已釋放，「失敗回執 → retry（清認領、世代+1）」可插隊（Codex 第二/
        四輪 high）。故檔案曝光**前**先重取列鎖並驗認領未被動過：過期 → 放棄且**不寫檔**
        （否則過期世代的檔案仍會曝光給 Turnkey，CAS 只保住 DB、保不住外部副作用）；完好 →
        持鎖寫檔＋確認——retry/record_result 需要同一列鎖，被序列化在本交易之後。
        寫檔為短本地 FS 操作，持鎖成本可接受（單店 outbox）。
        """
        locked = await self._repo.lock_queue_item(store_id, queue_id)
        assert locked is not None  # 認領已 commit，列必存在
        claim_intact = (
            locked.status is UploadStatus.PENDING
            and locked.xml_path == expected_path
            and locked.xml_sha256 == expected_sha
            and locked.attempts == expected_attempts
            and locked.dropped_at is None
        )
        if not claim_intact:
            # 狀態已由回執/retry 收斂：本次交付作廢、過期世代檔案不曝光。
            # 本分支無任何變更 → commit 純釋放列鎖（不可 rollback：會誤回同 session 內
            # 其他未 commit 的工作）。
            await self._session.commit()
            await self._session.refresh(locked)
            return locked

        result = dropper.drop(locked.message_type, filename, payload)
        if result.sha256 != expected_sha:
            await self._session.commit()  # 無列變更；釋放列鎖後回報
            raise EInvoiceDropError(f"佇列 {queue_id} 落檔 sha 與認領不符")
        locked.dropped_at = datetime.now(UTC)
        await self._session.commit()
        await self._session.refresh(locked)
        return locked

    # ── Amego 光貿 API 上送（docs/24）────────────────────────────────────────
    # 與 drop_pending 同屬「外部副作用出口」：自管交易邊界（先認領 commit、再打 API、
    # 再落結果）。HTTP 呼叫**不持列鎖**（不跨網路 I/O 持鎖）；in-flight 期間的作廢/回執
    # 競態由既有狀態機（VOID_PENDING／世代歸屬）收斂——Amego 只是把 Turnkey 的
    # 「拋檔→非同步回執」壓縮成「同步請求/回應」，暴露窗語意相同。

    _AMEGO_ENDPOINTS: ClassVar[dict[EInvoiceMessageType, str]] = {
        EInvoiceMessageType.F0401: "/json/f0401",
        EInvoiceMessageType.F0501: "/json/f0501",
        EInvoiceMessageType.G0401: "/json/g0401",
    }

    async def send_via_amego(
        self,
        store_id: int,
        queue_id: int,
        *,
        client: AmegoClient,
        allowed_message_types: Sequence[EInvoiceMessageType] | None = None,
    ) -> EInvoiceUploadQueue:
        """把 PENDING 佇列列上送 Amego（F0401 開立／F0501 作廢／G0401 折讓）。

        兩階段（Codex 第二輪）：
        1. **認領（鎖下守衛＋commit 先於曝光）**：xml_path 記 `amego:{endpoint}#a{n}`、
           sha＝data JSON、`amego_payload` 凍結整份 data JSON（重送 byte-for-byte——
           稅率變更/跨日不得讓同一 OrderId/AllowanceNumber 送出不同內容）、dropped_at＝
           可能已曝光起點。crash 於 API 前後，DB 都記得「這筆可能已送達平台」。
        2. **執行（重取鎖 CAS ＋ 持鎖打 API）**：重鎖 sale→queue 驗認領未變（並發送單
           在此序列化——後到者見已收斂終態即冪等回現狀、絕不重送）；持列鎖呼叫 Amego
           （單店、15s timeout 上限，鎖成本可接受），回應在同一鎖下落庫。

        傳輸失敗/曖昧回應（結果未知）→ 佇列維持 PENDING＋已認領、AmegoTransportError
        往外拋；下次呼叫對「已認領或世代>0 的 F0401」**先 invoice_query 對帳**：平台已有
        → 以查詢欄位補開立（無條碼/QR，證明聯不可印）、不重送；查無 → 以凍結 payload
        重送（OrderId 恆同，平台唯一約束擋重複開立）。F0501/G0401 已認領者以凍結 payload
        重送（作廢/折讓冪等由平台單號唯一性守護）。
        """
        # ── 階段 1：鎖下守衛＋認領 ──
        preview = await self._repo.get_queue_item(store_id, queue_id)
        if preview is None:
            raise EInvoiceQueueItemNotFound(f"佇列項目不存在或不屬於本店：id={queue_id}")
        sale_id = await self._resolve_sale_id(store_id, preview)
        if sale_id is not None:
            from app.modules.sales.service import SalesService  # 函式內 import 破循環

            await SalesService(self._session).lock_sale_row(store_id, sale_id)
        item = await self._repo.lock_queue_item(store_id, queue_id)
        if item is None:
            raise EInvoiceQueueItemNotFound(f"佇列項目不存在或不屬於本店：id={queue_id}")
        if item.status is not UploadStatus.PENDING:
            raise EInvoiceQueueNotDroppable(
                "這筆不是『等待送出』的狀態，不能再送一次，請重新整理頁面看最新狀態"
            )
        # 呼叫端限定的訊息型別必須**在鎖下重驗**（Codex 第二輪 P1）：背景送出在選單階段
        # 已篩過一次，但那是無鎖讀取；列可能在選單與此處之間被改成 F0401，於是「只送
        # 作廢/折讓」的界線會在最後一刻失守、背景真的去開一張發票。端點就是由這一行的
        # `item.message_type` 決定，界線必須貼著它、而且在同一把鎖內。
        if allowed_message_types is not None and item.message_type not in allowed_message_types:
            raise EInvoiceQueueNotDroppable(
                f"訊息類型 {item.message_type.value} 不在本次允許送出的範圍內"
            )
        endpoint = self._AMEGO_ENDPOINTS.get(item.message_type)
        if endpoint is None:
            raise EInvoiceQueueNotDroppable(f"訊息類型 {item.message_type.value} 不支援 Amego 上送")
        # 認領來源辨識（Codex 第廿五輪）：只有 Amego 認領（xml_path 前綴 amego:）才走凍結
        # payload 重送路徑。**非 amego: 前綴**＝舊 Turnkey outbox 認領（drop_pending 寫的
        # 檔案路徑，可能已曝光給 Turnkey）——不可經 Amego 上送（不同交付通道），以 409
        # 導向人工對帳；未認領（None）則走 Amego 全新認領。
        _AMEGO_CLAIM_PREFIX = "amego:"
        if item.xml_path is not None and not item.xml_path.startswith(_AMEGO_CLAIM_PREFIX):
            raise EInvoiceQueueNotDroppable(
                f"佇列 {queue_id} 由非 Amego 路徑認領（舊 Turnkey outbox），"
                "不可經 Amego 上送，需人工對帳"
            )
        already_claimed = item.xml_path is not None
        claim_attempts = item.attempts
        if not already_claimed:
            payload = await self._build_amego_payload(store_id, item)
            data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            item.xml_path = f"amego:{endpoint}#a{claim_attempts}"
            item.xml_sha256 = hashlib.sha256(data_json.encode("utf-8")).hexdigest()
            item.amego_payload = data_json
            item.dropped_at = datetime.now(UTC)
        await self._session.commit()  # 認領持久化（先於任何曝光）；釋放列鎖

        # ── 階段 2：重取鎖 CAS ＋ 持鎖打 API ──
        if sale_id is not None:
            from app.modules.sales.service import SalesService

            await SalesService(self._session).lock_sale_row(store_id, sale_id)
        locked = await self._repo.lock_queue_item(store_id, queue_id)
        assert locked is not None  # 認領已 commit，列必存在
        if not (
            locked.status is UploadStatus.PENDING
            and locked.attempts == claim_attempts
            and locked.xml_path is not None
        ):
            # 並發送單已把此列收斂（UPLOADED/FAILED）或 retry 換代：本次呼叫冪等回現狀、
            # 絕不重送過期世代（Codex 第二輪：兩並發送單只有一個真的打 API）。
            await self._session.commit()  # 無變更；純釋放列鎖
            await self._session.refresh(locked)
            return locked
        # 強制刷新目標（Codex 第五輪）：session expire_on_commit=False，認領 commit → 重取鎖
        # 的空窗內別的交易可能已把發票轉 VOID_PENDING（作廢先鎖 sale——所有發票狀態寫入者
        # 都持 sale 鎖，故此刻刷新後直到本交易 commit 前不會再變）。不刷新會以過期的
        # PENDING 走「→ISSUED」分支、漏排 F0501。
        if locked.invoice_id is not None:
            stale_invoice = await self._repo.get_invoice(store_id, locked.invoice_id)
            if stale_invoice is not None:
                await self._session.refresh(stale_invoice)
        if locked.allowance_id is not None:
            stale_allowance = await self._session.get(InvoiceAllowance, locked.allowance_id)
            if stale_allowance is not None:
                await self._session.refresh(stale_allowance)
        # 凍結 payload 的**存在性、checksum、解碼與身分抽取全部納入同一個受控錯誤邊界**：
        # 任何一處失敗都必須先把原因寫進 last_error，否則佇列卡在 PENDING 卻一片空白
        # （Codex 第四/五輪：sha 守衛原本在 try 外，先 commit 再拋，_note_blocked 不會執行）。
        try:
            frozen = locked.amego_payload
            if (
                frozen is None
                or hashlib.sha256(frozen.encode("utf-8")).hexdigest() != locked.xml_sha256
            ):
                raise EInvoiceDropError(
                    f"佇列 {queue_id} 認領 payload 遺失或與 sha 不符，拒絕重送（需人工對帳）"
                )
            try:
                payload = json.loads(frozen)
            except (ValueError, RecursionError) as exc:
                raise EInvoiceDropError(
                    f"佇列 {queue_id} 凍結 payload 無法解析（需人工對帳）"
                ) from exc
            # **一律對帳先行**（Codex 第三/七輪）：本地「是否已認領」快照可能過期——同列的另一
            # 呼叫可先插隊送出且結果未知（PENDING 依舊）；認領一旦 commit，平台就可能收過這則
            # 訊息。每次上送前先查平台實態：已套用 → 補記成功、絕不重送；**明確未套用**才送
            # （曖昧查詢回應由解析層拋 AmegoTransportError 擋下）。多一次查詢換確定性（單店）。
            if locked.action is EInvoiceAction.ISSUE and sale_id is not None:
                order_id = amego_order_id(store_id=store_id, sale_id=sale_id)
                _assert_payload_targets(payload, "OrderId", order_id, ctx="f0401")
                query_resp = await client.call(
                    "/json/invoice_query", build_invoice_query_data(order_id=order_id)
                )
                recovered = parse_query_issued(
                    query_resp,
                    expect_total=_payload_total(payload),
                    expect_not_before=locked.created_at,
                )
                if recovered is not None:
                    return await self._record_amego_outcome(
                        store_id,
                        queue_id,
                        success=True,
                        status_code="0",
                        message="以 invoice_query 對帳補開立（前次結果未知）",
                        delivery_attempt=claim_attempts,
                        issue_result=recovered,
                    )
                # 平台**明確查無**且發票已進作廢流程（空窗作廢）→ **取消開立**（Codex 第八
                # 輪）：作廢交易不得再產生真實稅務發票、靠事後 F0501 收拾——F0401 從未生效，
                # 佇列 CANCELLED、發票收斂 VOID、銷售同 F0401 失敗轉移（退貨觸發→NOT_ISSUED、
                # sale-void→no-op）。
                issue_target = await self._repo.get_invoice(store_id, locked.invoice_id or 0)
                if issue_target is not None and issue_target.status in (
                    InvoiceStatus.VOID_PENDING,
                    InvoiceStatus.VOID,
                ):
                    locked.status = UploadStatus.CANCELLED
                    locked.last_error = "平台查無且發票已作廢——取消開立（不重送 F0401）"
                    if issue_target.status is InvoiceStatus.VOID_PENDING:
                        issue_target.status = InvoiceStatus.VOID
                        from app.modules.sales.service import SalesService

                        await SalesService(self._session).mark_invoice_not_issued(
                            store_id, issue_target.sale_id
                        )
                    await self._session.commit()
                    await self._session.refresh(locked)
                    return locked
            elif locked.action is EInvoiceAction.VOID and locked.invoice_id is not None:
                void_target = await self._repo.get_invoice(store_id, locked.invoice_id)
                if void_target is not None and void_target.invoice_no:
                    _assert_payload_targets(
                        payload, "CancelInvoiceNumber", void_target.invoice_no, ctx="f0501"
                    )
                    query_resp = await client.call(
                        "/json/invoice_query",
                        build_invoice_query_by_number_data(invoice_number=void_target.invoice_no),
                    )
                    if parse_query_invoice_voided(query_resp, expect_total=void_target.total):
                        return await self._record_amego_outcome(
                            store_id,
                            queue_id,
                            success=True,
                            status_code="0",
                            message="以 invoice_query 對帳確認平台已作廢（前次結果未知）",
                            delivery_attempt=claim_attempts,
                            issue_result=None,
                        )
            elif locked.action is EInvoiceAction.ALLOWANCE and locked.allowance_id is not None:
                _assert_payload_targets(
                    payload,
                    "AllowanceNumber",
                    allowance_number(store_id=store_id, allowance_id=locked.allowance_id),
                    ctx="g0401",
                )
                query_resp = await client.call(
                    "/json/allowance_query",
                    build_allowance_query_data(
                        number=allowance_number(store_id=store_id, allowance_id=locked.allowance_id)
                    ),
                )
                sent_net, sent_tax, sent_original_no = _payload_allowance_identity(payload)
                if parse_query_allowance_exists(
                    query_resp,
                    expect_original_invoice_no=sent_original_no,
                    expect_net=sent_net,
                    expect_tax=sent_tax,
                    expect_not_before=locked.created_at,
                ):
                    return await self._record_amego_outcome(
                        store_id,
                        queue_id,
                        success=True,
                        status_code="0",
                        message="以 allowance_query 對帳確認平台已有折讓（前次結果未知）",
                        delivery_attempt=claim_attempts,
                        issue_result=None,
                    )

            # **送出前先持久化「已開始送出」**（docs/36）：從這一刻起平台就可能收到，
            # 手開登記必須改以此為準向平台求證。認領痕跡（xml_path/dropped_at）不夠——
            # 那在對帳查詢之前就寫了，查詢斷網失敗時其實什麼都沒送出。
            locked.posted_at = datetime.now(UTC)
            await self._session.commit()
            # commit 釋放**整個交易**（含 sale 鎖）。重取必須照全域鎖序 sale → queue
            # （反序會與作廢/退貨 AB-BA 死鎖），**並重跑與階段 2 相同的 CAS**：
            # 這個空窗內並發作廢可能已把發票轉 VOID_PENDING、把此列收斂，
            # 不重驗就會以過期世代硬送並在記錄結果時違反發票狀態約束。
            if sale_id is not None:
                from app.modules.sales.service import SalesService  # 函式內 import 破循環

                await SalesService(self._session).lock_sale_row(store_id, sale_id)
            locked = await self._repo.lock_queue_item(store_id, queue_id)
            assert locked is not None
            if not (
                locked.status is UploadStatus.PENDING
                and locked.attempts == claim_attempts
                and locked.xml_path is not None
            ):
                await self._session.commit()  # 無變更；純釋放鎖
                await self._session.refresh(locked)
                return locked
            # 同階段 2 的「強制刷新目標」：這個空窗內作廢可能已把發票轉 VOID_PENDING。
            # 不刷新就會以過期的 PENDING 走「→ISSUED」分支，寫出 status=ISSUED 卻仍有
            # void_reason 的列，直接違反 ck_invoices_void_reason_matches_status。
            # **不可在此中止**：已認領的 VOID_PENDING 仍要完成交付，再由結果轉移收斂
            # （既有併發測試期待平台開立成功後收斂 VOID_PENDING 並排 F0501）。
            if locked.invoice_id is not None:
                target = await self._repo.get_invoice(store_id, locked.invoice_id)
                if target is not None:
                    await self._session.refresh(target)
            if locked.allowance_id is not None:
                stale_allowance = await self._session.get(InvoiceAllowance, locked.allowance_id)
                if stale_allowance is not None:
                    await self._session.refresh(stale_allowance)
            resp = await client.call(endpoint, payload)  # 傳輸中斷 → 維持已認領 PENDING
            code = resp.get("code")
            # 曖昧回應不可當「平台拒絕」記 FAILED（Codex 第一/二輪）：平台可能已開立，誤標
            # FAILED 後 retry 會清認領、重送撞重複 OrderId。缺 code／非整數／**bool**（Python
            # bool 是 int 子類，JSON true/false 不得矇混）→ 結果未知，維持已認領待對帳。
            if type(code) is not int:
                raise AmegoTransportError("Amego 回應缺 code 或型別不明（結果不可信，待對帳）")
            success = code == 0
            issue_result: AmegoIssueResult | None = None
            if success and locked.action is EInvoiceAction.ISSUE:
                issue_result = parse_f0401_success(resp)  # 欄位不合法 → 不可信，維持已認領
        except (AmegoTransportError, EInvoiceDropError) as exc:
            # 涵蓋**整條結果未知／無法安全重送的路徑**（payload 解碼、身分抽取、對帳查詢、
            # 實際 POST、回應解析）：狀態維持 PENDING，但把原因寫進 last_error，
            # 否則只回 500/502、佇列上看不到卡住原因（Codex 第二/四輪）。
            await self._note_blocked(store_id, queue_id, str(exc))
            raise
        return await self._record_amego_outcome(
            store_id,
            queue_id,
            success=success,
            status_code=str(code),
            message=str(resp.get("msg") or "")[:500] or None,
            delivery_attempt=claim_attempts,
            issue_result=issue_result,
        )

    async def _note_blocked(self, store_id: int, queue_id: int, reason: str) -> None:
        """記下「本次為何不能上送」，**不改狀態**（維持 PENDING 待人工對帳）。

        對帳先行擋下時原本只回 502，佇列列上看不到原因；卡住的稅務訊息必須自帶說明。
        """
        item = await self._repo.lock_queue_item(store_id, queue_id)
        if item is not None:
            item.last_error = reason[:500]
        await self._session.commit()

    async def _record_amego_outcome(
        self,
        store_id: int,
        queue_id: int,
        *,
        success: bool,
        status_code: str,
        message: str | None,
        delivery_attempt: int,
        issue_result: AmegoIssueResult | None,
    ) -> EInvoiceUploadQueue:
        """把 Amego 回應落庫：先在鎖下補發票開立欄位（ISSUE 成功），再走 record_result
        （事件稽核＋佇列/發票/銷售狀態轉移），最後 commit。

        record_result 自帶 sale→queue 鎖序與世代歸屬（delivery_attempt）；in-flight 期間
        被 retry/作廢的過期回應會落稽核事件後以衝突收斂，不污染新世代。
        """
        if issue_result is not None:
            preview = await self._repo.get_queue_item(store_id, queue_id)
            if preview is not None and preview.invoice_id is not None:
                sale_id = await self._resolve_sale_id(store_id, preview)
                if sale_id is not None:
                    from app.modules.sales.service import SalesService

                    await SalesService(self._session).lock_sale_row(store_id, sale_id)
                locked = await self._repo.lock_queue_item(store_id, queue_id)
                # 回填守衛與 record_result 的世代/狀態檢查同步（Codex 第一輪 critical）：
                # 佇列已非 PENDING 或世代不符（in-flight 期間被並發送單標 FAILED / retry）
                # → 本回應屬過期交付，**不回填任何開立欄位**；record_result 會留稽核事件
                # 並以衝突收斂，不留「有字軌但未開立」的半套狀態。
                claim_intact = (
                    locked is not None
                    and locked.status is UploadStatus.PENDING
                    and locked.attempts == delivery_attempt
                )
                invoice = (
                    await self._repo.get_invoice(store_id, preview.invoice_id)
                    if claim_intact
                    else None
                )
                if invoice is not None:
                    if invoice.invoice_no is None:
                        invoice.invoice_no = issue_result.invoice_no
                        invoice.invoice_date = issue_result.invoice_date
                        invoice.invoice_time = issue_result.invoice_time
                        invoice.random_number = issue_result.random_number
                        invoice.barcode_text = issue_result.barcode_text
                        invoice.qrcode_left = issue_result.qrcode_left
                        invoice.qrcode_right = issue_result.qrcode_right
                        await self._session.flush()
                    elif invoice.invoice_no != issue_result.invoice_no:
                        await self._session.commit()  # 釋放鎖；不覆寫既有字軌
                        raise EInvoiceResultConflict(
                            f"平台回覆字軌 {issue_result.invoice_no} 與本地既有 "
                            f"{invoice.invoice_no} 不符，拒絕套用（需人工對帳）"
                        )
        try:
            item = await self.record_result(
                store_id,
                queue_id,
                success=success,
                status_code=status_code,
                message=message,
                source_ref="amego",
                delivery_attempt=delivery_attempt,
            )
        except (EInvoiceResultConflict, EInvoiceResultNotApplicable):
            await self._session.commit()  # 事件留稽核（router 慣例），衝突往外報
            raise
        # G0401 核可 → 把自編折讓單號寫回（供對帳/後續 g0501）。
        if success and item.action is EInvoiceAction.ALLOWANCE and item.allowance_id is not None:
            allowance = await self._session.get(InvoiceAllowance, item.allowance_id)
            if allowance is not None and allowance.allowance_no is None:
                allowance.allowance_no = allowance_number(
                    store_id=store_id, allowance_id=allowance.id
                )
        await self._session.commit()
        await self._session.refresh(item)
        return item

    async def reprint_payload_for_sale(
        self,
        store_id: int,
        sale_id: int,
        *,
        client_factory: Callable[[], Awaitable[AmegoClient]],
    ) -> ProofPrintPayload:
        """向 Amego 取這筆銷售的發票證明聯**補印內容**（base64 ESC/POS）。

        **為什麼不自己組版面**：證明聯的二維條碼含一段以財政部金鑰加密的驗證資訊，
        那把鑰匙在加值中心手上。`invoice_query` 也只回隨機碼、不回條碼內容
        （已對真平台實測）。所以補印一律由平台產生整張版面，我們原樣轉送印表機。

        僅限**平台已開立**者：手開紙本的證明聯是店員手寫那張、平台上沒有；
        未開立的更沒有內容可印。

        **正本還是補印，由「印出來過沒有」決定**（電子發票實施作業要點 §26）：
        證明聯以列印一次為限，從未印出（例：開立成功但回應斷線）時那一次還沒用掉，
        要印**正本**；已印過才印補印——補印會加註「補印」二字，且依法須併同原聯
        才能兌獎，誤用等於給客人一張兌不了獎的紙。
        """
        invoice = await self._repo.find_invoice_by_sale(store_id, sale_id)
        if invoice is None:
            raise InvoiceNotFound(f"銷售 {sale_id} 無發票")
        if invoice.status is not InvoiceStatus.ISSUED:
            raise InvoiceNotIssued(f"發票狀態 {invoice.status.value}，尚未開立，無證明聯可補印")
        if invoice.issue_channel is EInvoiceIssueChannel.MANUAL_PAPER:
            raise ManualPaperInvoiceOperation(
                "本筆為手開紙本發票，證明聯是當初手寫的那張，系統無法補印"
            )
        is_reprint = invoice.proof_printed_at is not None
        client = await client_factory()
        resp = await client.call(
            "/json/invoice_print",
            build_invoice_print_data(
                order_id=amego_order_id(store_id=store_id, sale_id=sale_id),
                printer_type=AMEGO_PRINTER_TYPE_TM_T82III,
                # **一律印正本、不加註「補印」**（店主 2026-08-29 裁示）。
                # 要點 §26 的註記本意是讓重複那張不能再兌獎，拿掉後避免重複兌領的責任
                # 回到營業人身上。取捨理由：現實裡「客人弄丟／缺紙漏印」遠比重複兌獎
                # 常見，而給沒拿過正本的客人一張寫著「補印」的紙，那張永遠兌不了獎。
                # `is_reprint` 仍如實回報，供畫面提醒店員這張之前印過。
                reprint=False,
            ),
        )
        return ProofPrintPayload(parse_invoice_print(resp), is_reprint)

    async def mark_proof_printed(
        self, store_id: int, sale_id: int, *, actor_user_id: int | None = None
    ) -> None:
        """記下證明聯已實際印出（印表機回報成功後呼叫）。

        **為什麼不在產出內容時就記**：那正是要修的失效樣態——內容拿到了、紙沒出來。
        以印表機回報為準，才不會把一次失敗的列印算掉「列印一次」的額度。

        每次都寫稽核（CLAUDE.md §5）：`copy` 記的是這一張到底是正本還是補印。
        營業人補印導致重複兌領獎金時責任在營業人身上，事後要舉證「印的是補印」
        就靠這筆——同模組的作廢與手開登記都有寫，補印沒有理由例外。
        """
        invoice = await self._repo.find_invoice_by_sale(store_id, sale_id)
        if invoice is None:
            raise InvoiceNotFound(f"銷售 {sale_id} 無發票")
        printed_before = invoice.proof_printed_at
        await self._repo.mark_proof_printed(invoice, datetime.now(tz=UTC))
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="PRINT_INVOICE_PROOF",
            entity_type="invoice",
            entity_id=str(invoice.id),
            after={
                "sale_id": sale_id,
                "invoice_no": invoice.invoice_no,
                # 第一次列印＝正本（「列印一次為限」的那一次）；之後一律補印。
                "copy": "REPRINT" if printed_before is not None else "ORIGINAL",
            },
        )

    async def issue_for_sale(
        self,
        store_id: int,
        sale_id: int,
        *,
        client_factory: Callable[[], Awaitable[AmegoClient]],
    ) -> Invoice:
        """POS 結帳後開立入口：把該銷售的發票上送 Amego，回開立後發票（冪等）。

        已 ISSUED → 直接回（POS 重試/重印取號用）；PENDING → 送其 F0401 佇列列
        （FAILED 列先 retry 轉回 PENDING 再送，POS 一鍵重試）；其他狀態（作廢中/已作廢）
        → EInvoiceQueueNotDroppable。平台明確拒絕 → AmegoIssueFailed（佇列已 FAILED、
        留 last_error）；傳輸中斷 → AmegoTransportError（已認領，之後對帳）。

        **客戶端延遲建立**（同 `register_manual_invoice`）：已開立的發票（含手開紙本）
        答案就在本地，不需要也不該被憑證卡住。先建客戶端會讓「AMEGO_APP_KEY 未載到」
        變成 409，連「本筆已登記手開紙本」都讀不回來——而連不上平台正是店家改開紙本的
        原因，等於這功能最需要它的時候失效（Codex 對抗審查第十輪 high）。
        """
        invoice = await self._repo.find_invoice_by_sale(store_id, sale_id)
        if invoice is None:
            raise InvoiceNotFound(f"銷售 {sale_id} 無發票（einvoice 未啟用或非本店）")
        if invoice.status is InvoiceStatus.ISSUED:
            return invoice
        if invoice.status is not InvoiceStatus.PENDING:
            raise EInvoiceQueueNotDroppable(f"發票狀態 {invoice.status.value}，不可開立")
        # 全域鎖序 sale → queue（Codex 第四輪）：先鎖 sale 再鎖佇列列——作廢/退貨路徑
        # 先鎖 sale 再動佇列，此處先鎖佇列會與其 AB-BA 死鎖（結帳後立即作廢的窗口）。
        from app.modules.sales.service import SalesService  # 函式內 import 破循環

        await SalesService(self._session).lock_sale_row(store_id, sale_id)
        # 鎖後刷新（Codex 第六輪）：等鎖期間另一請求可能已完成開立（POS 雙擊/重試）——
        # 過期的 PENDING 會誤判「無可上送佇列列」丟 404，或回無字軌的 stale 發票。
        await self._session.refresh(invoice)
        # refresh 會改動 status，重新讀一次（也讓 mypy 解除先前的型別窄化）。
        locked_status: InvoiceStatus = invoice.status
        if locked_status is InvoiceStatus.ISSUED:
            return invoice  # 並發贏家已開立 → 冪等回原發票（取號印證明聯）
        if locked_status is not InvoiceStatus.PENDING:
            raise EInvoiceQueueNotDroppable(f"發票狀態 {locked_status.value}，不可開立")
        issue_item = next(
            (
                i
                for i in await self._repo.lock_queue_items_for_invoice(store_id, invoice.id)
                if i.action is EInvoiceAction.ISSUE
                and i.status in (UploadStatus.PENDING, UploadStatus.FAILED)
            ),
            None,
        )
        if issue_item is None:
            raise EInvoiceQueueItemNotFound(f"發票 {invoice.id} 無可上送的開立佇列列")
        if issue_item.status is UploadStatus.FAILED:
            await self.retry(store_id, issue_item.id)
        sent = await self.send_via_amego(store_id, issue_item.id, client=await client_factory())
        if sent.status is not UploadStatus.UPLOADED:
            raise AmegoIssueFailed(f"Amego 拒絕開立：{sent.last_error or '未知錯誤'}（可稍後重試）")
        refreshed = await self._repo.get_invoice(store_id, invoice.id)
        assert refreshed is not None
        await self._session.refresh(refreshed)  # send 自管 commit，identity map 需刷新
        return refreshed

    async def _build_amego_payload(self, store_id: int, item: EInvoiceUploadQueue) -> object:
        """依佇列列組對應 Amego payload（守衛：目標狀態必須可上送）。"""
        if item.message_type is EInvoiceMessageType.G0401:
            if item.allowance_id is None:
                raise EInvoiceQueueNotDroppable("G0401 佇列列缺折讓目標")
            allowance = await self._session.get(InvoiceAllowance, item.allowance_id)
            if allowance is None or allowance.store_id != store_id:
                raise EInvoiceQueueItemNotFound(f"折讓不存在或不屬於本店：id={item.allowance_id}")
            if allowance.voided:
                raise EInvoiceQueueNotDroppable(f"折讓 {allowance.id} 已作廢，不可上送")
            invoice = await self._repo.get_invoice(store_id, allowance.invoice_id)
            if invoice is None:
                raise InvoiceNotFound(f"發票不存在或不屬於本店：id={allowance.invoice_id}")
            # 折讓日＝**折讓發生的那天**，不是我們把它送出去的那天（Codex 第四輪 P1）。
            # 自動送出上線前這條沒被踩到（根本沒人送）；現在若因平台故障或店休積壓了
            # 幾週，用送出日會把折讓申報進錯的期別——跨月即申報錯誤。
            allowance_created = (
                allowance.created_at
                if allowance.created_at.tzinfo is not None
                else allowance.created_at.replace(tzinfo=UTC)
            )
            return build_g0401_data(
                number=allowance_number(store_id=store_id, allowance_id=allowance.id),
                allowance_date=allowance_created.astimezone(_TAIPEI_TZ).date(),
                invoice=invoice,
                net=Decimal(allowance.net),
                tax=Decimal(allowance.tax),
            )
        invoice = await self._repo.get_invoice(store_id, item.invoice_id or 0)
        if invoice is None:
            raise InvoiceNotFound(f"發票不存在或不屬於本店：id={item.invoice_id}")
        if item.message_type is EInvoiceMessageType.F0501:
            if not invoice.invoice_no:
                raise EInvoiceQueueNotDroppable(
                    f"發票 {invoice.id} 尚無字軌號碼，不可送作廢（F0501）"
                )
            return build_f0501_data(invoice.invoice_no)
        # F0401：僅待開立可送；已認領的 VOID_PENDING 允許恢復完成交付（同 _serialize 規則）。
        if invoice.status is not InvoiceStatus.PENDING:
            claimed_recovery = (
                invoice.status is InvoiceStatus.VOID_PENDING and item.xml_path is not None
            )
            if not claimed_recovery:
                raise EInvoiceQueueNotDroppable(
                    f"發票 {invoice.id} 非待開立（{invoice.status.value}），不可送開立"
                )
        from app.modules.sales.service import SalesService  # 函式內 import 破循環

        lines = await SalesService(self._session).get_lines(invoice.sale_id)
        # 金額/稅率一律用發票**落地快照**（invoice.net/tax/tax_rate），不讀活 settings
        # （結帳後改稅率不得改變申報內容，Codex 第九輪）。
        return build_f0401_data(
            invoice,
            lines,
            order_id=amego_order_id(store_id=store_id, sale_id=invoice.sale_id),
        )

    async def retry(self, store_id: int, queue_id: int) -> EInvoiceUploadQueue:
        """把 FAILED 佇列列轉回 PENDING（attempts+1），供重新拋檔/上傳。

        不觸碰發票與字軌號碼——重送絕不為同一筆銷售產生第二個發票號碼（不變量 2）。
        清掉上次拋檔痕跡（xml_path/sha256/dropped_at），使可重新拋檔。

        **終態目標不可復活**（Codex 第六輪）：F0401 失敗時若發票已在 VOID_PENDING
        （失敗轉移已把發票收斂為 VOID），retry 會造出「PENDING 但拋檔被拒、回執無法歸屬」
        的永久掛列——目標發票已 VOID／折讓已作廢者一律拒絕重送，列維持 FAILED 供稽核。
        """
        item = await self._repo.lock_queue_item(store_id, queue_id)
        if item is None:
            raise EInvoiceQueueItemNotFound(f"佇列項目不存在或不屬於本店：id={queue_id}")
        if item.status is not UploadStatus.FAILED:
            raise EInvoiceQueueNotRetryable(
                "只有『失敗』的項目可以重送。這筆目前不是失敗狀態，請重新整理頁面看最新狀態"
            )
        if item.action is EInvoiceAction.ISSUE and item.invoice_id is not None:
            invoice = await self._repo.get_invoice(store_id, item.invoice_id)
            if invoice is not None and invoice.status is InvoiceStatus.VOID:
                raise EInvoiceQueueNotRetryable(
                    f"發票 {invoice.id} 已作廢，開立訊息不可重送（此列維持 FAILED 供稽核）"
                )
        if item.allowance_id is not None:
            allowance = await self._session.get(InvoiceAllowance, item.allowance_id)
            if allowance is not None and allowance.voided:
                raise EInvoiceQueueNotRetryable(
                    f"折讓 {allowance.id} 已作廢，折讓訊息不可重送（此列維持 FAILED 供稽核）"
                )
            # 母發票已進入作廢流程 → 折讓不可再送（Codex 對抗審查 #1）：否則會同時對同一張
            # 發票送出 G0401 與 F0501，帳目自相矛盾且無法事後判斷孰先孰後。
            if allowance is not None:
                parent = await self._repo.get_invoice(store_id, allowance.invoice_id)
                if parent is not None and parent.status in (
                    InvoiceStatus.VOID,
                    InvoiceStatus.VOID_PENDING,
                ):
                    raise EInvoiceQueueNotRetryable(
                        "這張發票已經作廢（或正在作廢），不能再送折讓單——"
                        "同一張發票不可以既作廢又折讓。這筆折讓保留紀錄不再重送。"
                    )
        item.status = UploadStatus.PENDING
        item.attempts += 1
        item.last_error = None
        item.xml_path = None
        item.xml_sha256 = None
        item.amego_payload = None  # 明確失敗後的新世代允許以當下狀態重建內容
        item.dropped_at = None
        await self._session.flush()
        await self._session.refresh(item)  # onupdate updated_at 由 DB 設，刷回避免 lazy IO
        return item

    async def record_result(
        self,
        store_id: int,
        queue_id: int,
        *,
        success: bool,
        kind: str = RESULT_KIND_PROCESS,
        status_code: str | None = None,
        message: str | None = None,
        source_ref: str | None = None,
        delivery_attempt: int | None = None,
    ) -> EInvoiceUploadQueue:
        """記錄一筆 Turnkey 回執並（依 ProcessResult）更新佇列/發票狀態。

        - 一律先落庫回執事件（append-only 稽核）。
        - **SummaryResult（kind=SUMMARY）只作批次對帳，不改單筆狀態**（docs/18 §7.3）。
        - **ProcessResult（kind=PROCESS）** 才驅動單筆狀態，且只對「已拋檔且仍 PENDING」的列：
          成功 → 佇列 UPLOADED，並依動作轉發票狀態（見下）；失敗 → 佇列 FAILED（可 retry）。
          - ISSUE（F0401）核可 → 發票 PENDING→ISSUED，並同步對應 sale.invoice_status→ISSUED。
            但發票缺開立必要欄位（字軌號碼/開立日/開立時間/隨機碼）→ InvoiceIncompleteForIssue，
            狀態機拒絕把不完整發票標成 ISSUED（M1；配號/序列化齊備後才會成立）。
          - VOID（F0501）核可 → 發票 VOID_PENDING→VOID（此時平台才真正作廢，H3）。
          - ALLOWANCE（G0401）核可 → 不改發票狀態（折讓為獨立單）。
        - 終態列（UPLOADED/FAILED/CANCELLED）的回執**冪等處理**（Codex adversarial：importer
          重試/重複掃回執是常態，不可在 409 上打轉、更不可回滾掉稽核事件）：
          - 與終態一致的重複回執（UPLOADED×success / FAILED×failure）→ 接受：事件留檔、
            回現狀、不改狀態、不拋例外。
          - 矛盾的遲到回執 → 事件留檔（flush）後拋 EInvoiceResultConflict；呼叫端應 commit
            保留事件再回報衝突（router 已如此），終態不變更。
        - 未認領（xml_path NULL——檔案不可能曝光過）→ EInvoiceResultNotApplicable。
          已認領未確認（crash 於拋檔中途）的回執照常受理——檔案可能已被 Turnkey 撿走。
        - **交付世代歸屬**（Codex 第二/三輪）：`delivery_attempt` 有帶且 ≠ 當前 attempts →
          舊世代回執（retry 前的交付）——事件留稽核後拋 EInvoiceResultConflict，絕不套用到
          新世代（舊失敗不可誤殺新嘗試、舊成功不可把已改內容的新發票標 ISSUED）。
          **retry 過的列（attempts > 0）狀態性回執必帶世代**：省略即無法歸屬（可能是任一舊
          世代的遲到回執）→ 同樣留稽核＋衝突、不改狀態——歸屬不得依賴呼叫端自律。從未 retry
          （attempts == 0）只有一個世代、省略無歧義，維持手動方便。T13 importer 必自檔名
          （…-a{n}.xml）解出世代帶入。稽核事件照實記錄呼叫端所帶世代（未帶存 NULL，不竄補）。

        自動解析 Turnkey 回執檔的 importer 待收尾階段依 3.9 手冊實作；此為結果落庫共用出口。
        """
        # 全域鎖序 sale → queue（Codex 第六輪）：狀態性回執可能觸及 sale（mark_invoice_*），
        # 而作廢/退貨路徑先鎖 sale 再鎖佇列——此處先以無鎖讀解析關聯 sale（queue→invoice→
        # sale_id 皆不可變欄位）、鎖 sale，再鎖佇列列，否則兩路徑 AB-BA 死鎖。
        if kind == RESULT_KIND_PROCESS:
            preview = await self._repo.get_queue_item(store_id, queue_id)
            if preview is None:
                raise EInvoiceQueueItemNotFound(f"佇列項目不存在或不屬於本店：id={queue_id}")
            sale_id = await self._resolve_sale_id(store_id, preview)
            if sale_id is not None:
                from app.modules.sales.service import SalesService  # 函式內 import 破循環

                await SalesService(self._session).lock_sale_row(store_id, sale_id)

        item = await self._repo.lock_queue_item(store_id, queue_id)
        if item is None:
            raise EInvoiceQueueItemNotFound(f"佇列項目不存在或不屬於本店：id={queue_id}")

        await self._repo.add_result_event(
            EInvoiceResultEvent(
                store_id=store_id,
                queue_id=queue_id,
                result_kind=kind,
                success=success,  # 權威成敗（status_code/message 選填，稽核須可獨立證明結果）
                status_code=status_code,
                message=message,
                source_ref=source_ref,
                delivery_attempt=delivery_attempt,  # 照實記錄（未帶存 NULL，稽核不說謊）
            )
        )
        # SummaryResult：僅對帳、不改單筆狀態。
        if kind == RESULT_KIND_SUMMARY:
            await self._session.flush()
            await self._session.refresh(item)
            return item

        # 世代歸屬（留稽核、不套用；呼叫端 commit 保留事件再回報衝突，router 已如此）：
        # (a) 帶了但不符 → 舊世代回執；(b) retry 過卻沒帶 → 無法歸屬，不得預設為當前世代
        #     （Codex 第三輪：a0 遲到成功省略世代會被誤套到 a1）。
        if delivery_attempt is not None and delivery_attempt != item.attempts:
            await self._session.flush()
            raise EInvoiceResultConflict(
                f"回執屬於交付世代 a{delivery_attempt}，佇列目前為 a{item.attempts}"
                "（已 retry）；事件已留稽核、不套用"
            )
        if delivery_attempt is None and item.attempts > 0:
            await self._session.flush()
            raise EInvoiceResultConflict(
                f"佇列已 retry（目前世代 a{item.attempts}），狀態性回執必須帶 "
                "delivery_attempt 以歸屬世代；事件已留稽核、不套用"
            )

        # ProcessResult：終態列冪等/留證，不覆寫（見 docstring）。
        if item.status is not UploadStatus.PENDING:
            duplicate_same_outcome = (item.status is UploadStatus.UPLOADED and success) or (
                item.status is UploadStatus.FAILED and not success
            )
            await self._session.flush()  # 事件先落庫（append-only 稽核，不因例外遺失）
            if duplicate_same_outcome:
                await self._session.refresh(item)
                return item  # 冪等接受重複回執
            raise EInvoiceResultConflict(
                f"佇列項目已達終態（{item.status.value}），與回執"
                f"（success={success}）矛盾；事件已留稽核、終態不變更"
            )
        if item.xml_path is None:
            raise EInvoiceResultNotApplicable("佇列項目尚未認領拋檔，不應有平台回執")

        if success:
            # 發票狀態轉移（守衛先行；失敗則整筆不變）。
            await self._apply_success_transition(store_id, item)
            item.status = UploadStatus.UPLOADED
            item.uploaded_at = datetime.now(UTC)
            item.last_error = None
        else:
            await self._apply_failure_transition(store_id, item)
            item.status = UploadStatus.FAILED
            item.last_error = message
        await self._session.flush()
        await self._session.refresh(item)  # onupdate updated_at 由 DB 設，刷回避免 lazy IO
        return item

    @staticmethod
    def _assert_issue_fields(invoice: Invoice) -> None:
        """M1：開立/作廢前必須有開立必要欄位（MIG F0401 必填 InvoiceNumber/Date/Time + 隨機碼）。"""
        if not (
            invoice.invoice_no
            and invoice.invoice_date is not None
            and invoice.invoice_time
            and invoice.random_number
        ):
            raise InvoiceIncompleteForIssue(
                f"發票 {invoice.id} 缺開立必要欄位（字軌/日期/時間/隨機碼），不可標為已開立"
            )

    async def _apply_success_transition(self, store_id: int, item: EInvoiceUploadQueue) -> None:
        """ProcessResult 成功時依佇列動作轉對應狀態（ISSUE / VOID / ALLOWANCE）。"""
        if item.action is EInvoiceAction.ALLOWANCE:
            # G0401（折讓）核可 → 銷售 PENDING_ALLOWANCE→ALLOWANCE（比照 ISSUE/VOID 等平台成功）。
            await self._mark_sale_allowance(store_id, item)
            return
        if item.invoice_id is None:
            return
        invoice = await self._repo.get_invoice(store_id, item.invoice_id)
        if invoice is None:
            return
        from app.modules.sales.service import SalesService  # 函式內 import 破 sales↔einvoice 循環

        if item.action is EInvoiceAction.ISSUE:
            if invoice.status is InvoiceStatus.PENDING:
                self._assert_issue_fields(invoice)
                invoice.status = InvoiceStatus.ISSUED
                # H2：同步對應銷售 PENDING_ISSUE→ISSUED。
                await SalesService(self._session).mark_invoice_issued(store_id, invoice.sale_id)
                # 核可前已有（部分）退貨的補開折讓：發票此刻才成立，先前退貨因「非 ISSUED」
                # 未能開折讓 → 於此回補 G0401（returns↔einvoice 互呼，函式內 import 破循環）。
                from app.modules.returns.service import ReturnsService

                await ReturnsService(self._session).backfill_allowances_for_issued_sale(
                    store_id, invoice.sale_id
                )
            elif invoice.status is InvoiceStatus.VOID_PENDING:
                # 作廢請求先於 F0401 回執送達：平台仍開立了 → 續排 F0501 作廢（留 VOID_PENDING）。
                self._assert_issue_fields(invoice)
                await self._enqueue_f0501(store_id, invoice.id)
        elif item.action is EInvoiceAction.VOID and invoice.status is InvoiceStatus.VOID_PENDING:
            invoice.status = InvoiceStatus.VOID  # H3：F0501 核可後才正式作廢
            await self._audit_invoice_void_transition(
                store_id, invoice, status_before=InvoiceStatus.VOID_PENDING, source="F0501_ACCEPTED"
            )
            # 反正規化收斂：曾取得字軌（平台真的開過）→ VOID；從未取得 → NOT_ISSUED。
            sales = SalesService(self._session)
            if invoice.invoice_no is not None:
                await sales.mark_invoice_voided(store_id, invoice.sale_id)
            else:
                await sales.mark_invoice_not_issued(store_id, invoice.sale_id)

    async def _apply_failure_transition(self, store_id: int, item: EInvoiceUploadQueue) -> None:
        """ProcessResult 失敗時的狀態收斂：作廢請求中的 F0401 失敗 → 平台未開立 → 正式 VOID。"""
        if item.action is not EInvoiceAction.ISSUE or item.invoice_id is None:
            return
        invoice = await self._repo.get_invoice(store_id, item.invoice_id)
        if invoice is not None and invoice.status is InvoiceStatus.VOID_PENDING:
            invoice.status = InvoiceStatus.VOID
            await self._audit_invoice_void_transition(
                store_id, invoice, status_before=InvoiceStatus.VOID_PENDING, source="F0401_FAILED"
            )
            # F0401 失敗＝平台從未開立 → 該銷售最終沒有有效發票。
            from app.modules.sales.service import SalesService

            await SalesService(self._session).mark_invoice_not_issued(store_id, invoice.sale_id)

    async def _mark_sale_allowance(self, store_id: int, item: EInvoiceUploadQueue) -> None:
        """G0401 核可 → 找到對應銷售、標 PENDING_ALLOWANCE→ALLOWANCE（跨模組經 sales service）。

        同一發票尚有其他折讓在途（PENDING）時不轉——待全部折讓核可才轉正式 ALLOWANCE，
        避免第一張核可就把 sale 標成已折讓、第二張其實還沒被平台接受。
        """
        if item.allowance_id is None:
            return
        allowance = await self._session.get(InvoiceAllowance, item.allowance_id)
        if allowance is None or allowance.store_id != store_id:
            return
        invoice = await self._repo.get_invoice(store_id, allowance.invoice_id)
        if invoice is None:
            return
        others = await self._repo.count_other_unresolved_allowance_items(
            store_id, invoice.id, exclude_queue_id=item.id
        )
        if others > 0:
            return  # 其他折讓未成功終結（含 FAILED），sale 級狀態維持 PENDING_ALLOWANCE
        from app.modules.sales.service import SalesService  # 函式內 import 破 sales↔einvoice 循環

        await SalesService(self._session).mark_invoice_allowance(store_id, invoice.sale_id)

    async def _resolve_sale_id(self, store_id: int, item: EInvoiceUploadQueue) -> int | None:
        """佇列列 → 關聯 sale_id（invoice 直連或經 allowance→invoice；欄位皆不可變）。"""
        invoice_id = item.invoice_id
        if invoice_id is None and item.allowance_id is not None:
            allowance = await self._session.get(InvoiceAllowance, item.allowance_id)
            if allowance is not None and allowance.store_id == store_id:
                invoice_id = allowance.invoice_id
        if invoice_id is None:
            return None
        invoice = await self._repo.get_invoice(store_id, invoice_id)
        return invoice.sale_id if invoice is not None else None

    async def get_invoice(self, store_id: int, invoice_id: int) -> Invoice:
        invoice = await self._repo.get_invoice(store_id, invoice_id)
        if invoice is None:
            raise InvoiceNotFound(f"發票不存在或不屬於本店：id={invoice_id}")
        return invoice

    async def get_invoice_for_sale(self, store_id: int, sale_id: int) -> Invoice | None:
        """某銷售的發票（無則 None）；供退貨判斷是否已開票、決定是否走 G0401 折讓（§7.5）。"""
        return await self._repo.find_invoice_by_sale(store_id, sale_id)

    async def has_settled_allowance(self, store_id: int, invoice_id: int) -> bool:
        """該發票是否已有**成功**的折讓（平台已核可）。

        只要有，後續退貨一律繼續折讓、不得再作廢原發票——否則會同時存在「已作廢的原發票」
        與先前開出的折讓單（見 ADR-014）。
        """
        for item in await self._repo.list_allowance_queue_items_for_invoice(store_id, invoice_id):
            if item.status is UploadStatus.UPLOADED:
                return True
        return False

    async def has_open_allowance(self, store_id: int, invoice_id: int) -> bool:
        """該發票是否有**尚未收斂**的折讓：在途（PENDING）或失敗但仍可重送（FAILED）。

        FAILED 必須算數（Codex 對抗審查 #1）：那張 G0401 隨時可能被店員重送成功，若不算作
        「既有折讓」，後續累計全退會誤走作廢，最終同時存在 G0401 與 F0501。
        分次退貨本來就會有多張 G0401 同時在途（系統已能正確收斂），故此旗標**只用來擋作廢**，
        不擋再開折讓。
        """
        return any(
            item.status in (UploadStatus.PENDING, UploadStatus.FAILED)
            for item in await self._repo.list_allowance_queue_items_for_invoice(
                store_id, invoice_id
            )
        )

    async def has_inflight_void(self, store_id: int, invoice_id: int) -> bool:
        """該發票是否有作廢（F0501）在途或結果未知——此時任何稅務動作都不得再疊加。"""
        return any(
            item.action is EInvoiceAction.VOID and item.status is UploadStatus.PENDING
            for item in await self._repo.list_queue_items_for_invoice(store_id, invoice_id)
        )

    async def get_allowance_for_return(
        self, store_id: int, return_id: int
    ) -> InvoiceAllowance | None:
        """某退貨單的既有折讓（無則 None）；供補開折讓時冪等判斷。"""
        return await self._repo.find_allowance_by_return(store_id, return_id)

    async def list_queue(
        self,
        store_id: int,
        *,
        status: UploadStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[EInvoiceUploadQueue]:
        return await self._repo.list_queue(store_id, status=status, limit=limit, offset=offset)

    async def list_queue_with_context(
        self,
        store_id: int,
        *,
        status: UploadStatus | None = None,
        stalled_before: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[tuple[EInvoiceUploadQueue, str | None, int | None]]:
        """佇列列＋發票號碼／交易編號（佇列頁用）。

        `stalled_before` 有值＝只列「需要人處理」的（與導覽列紅點同口徑）。
        """
        return await self._repo.list_queue_with_context(
            store_id,
            status=status,
            needs_attention=(_AUTO_SEND_ACTIONS, stalled_before) if stalled_before else None,
            limit=limit,
            offset=offset,
        )

    async def get_queue_item(self, store_id: int, queue_id: int) -> EInvoiceUploadQueue | None:
        """取單一佇列列（供背景送出於送出前再確認範圍）。"""
        return await self._repo.get_queue_item(store_id, queue_id)

    async def record_auto_send_error(self, store_id: int, queue_id: int, message: str) -> None:
        """把自動送出的失敗原因寫到佇列列（狀態不動，設定修好即自行重試）。"""
        item = await self._repo.get_queue_item(store_id, queue_id)
        if item is None:
            return
        item.last_error = message

    async def count_needing_attention(self, store_id: int, *, stalled_before: datetime) -> int:
        """需要人處理的筆數：平台退回的，加上**卡太久沒送出**的作廢/折讓。

        只數 FAILED 不夠——設定漏帶時佇列列會停在 PENDING、永遠不轉 FAILED，
        於是帳上作廢、平台有效，而畫面一片安靜（Codex 第五輪 P1）。
        """
        return await self._repo.count_needing_attention(
            store_id, auto_send_actions=_AUTO_SEND_ACTIONS, stalled_before=stalled_before
        )

    async def count_queue(
        self,
        store_id: int,
        *,
        status: UploadStatus | None = None,
        stalled_before: datetime | None = None,
    ) -> int:
        """符合篩選的佇列總筆數。"""
        return await self._repo.count_queue(
            store_id,
            status=status,
            needs_attention=(_AUTO_SEND_ACTIONS, stalled_before) if stalled_before else None,
        )

    async def list_due_auto_send_items(
        self,
        *,
        actions: Sequence[EInvoiceAction],
        message_types: Sequence[EInvoiceMessageType],
        idle_since: datetime,
        limit: int,
    ) -> list[EInvoiceUploadQueue]:
        """跨店取到期可自動送出的待送出佇列列（供背景送出；範圍界定見 background_service）。"""
        return await self._repo.list_due_auto_send_items(
            actions=actions,
            message_types=message_types,
            idle_since=idle_since,
            limit=limit,
        )

    async def _serialize(
        self,
        store_id: int,
        item: EInvoiceUploadQueue,
        serializer: InvoiceXmlSerializer,
    ) -> bytes:
        """依佇列列目標（發票/折讓）呼叫對應序列化；已作廢目標拒絕拋檔。"""
        if item.allowance_id is not None:
            allowance = await self._session.get(InvoiceAllowance, item.allowance_id)
            if allowance is None or allowance.store_id != store_id:
                raise EInvoiceQueueItemNotFound(f"折讓不存在或不屬於本店：id={item.allowance_id}")
            if allowance.voided:
                raise EInvoiceQueueNotDroppable(f"折讓 {allowance.id} 已作廢，不可拋檔")
            return serializer.serialize_allowance(allowance, item.message_type)
        invoice = await self._repo.get_invoice(store_id, item.invoice_id or 0)
        if invoice is None:
            raise InvoiceNotFound(f"發票不存在或不屬於本店：id={item.invoice_id}")
        # 開立（F0401）只在發票仍待開立（PENDING）時可拋。例外（Codex 第五輪）：**已認領**的
        # F0401 在 VOID_PENDING 下允許恢復完成交付——認領後檔案可能已曝光、無從得知（crash 於
        # 寫檔前後皆有可能），唯有補完交付讓平台回執必然到來，才能由「F0401 成功→續 F0501／
        # 失敗→VOID」收斂；否則該列永遠 PENDING 而無回執、發票卡死 VOID_PENDING。
        # 其餘（ISSUED/VOID、或未認領的 VOID_PENDING）不得拋開立。作廢（F0501）本就針對
        # VOID_PENDING 發票，放行。
        if item.action is EInvoiceAction.ISSUE and invoice.status is not InvoiceStatus.PENDING:
            claimed_recovery = (
                invoice.status is InvoiceStatus.VOID_PENDING and item.xml_path is not None
            )
            if not claimed_recovery:
                raise EInvoiceQueueNotDroppable(
                    f"發票 {invoice.id} 非待開立（{invoice.status.value}），不可拋開立訊息"
                )
        return serializer.serialize_invoice(invoice, item.message_type)
