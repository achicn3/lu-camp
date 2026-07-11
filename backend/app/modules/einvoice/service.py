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
from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import split_tax_inclusive
from app.modules.einvoice.amego import (
    AmegoClient,
    AmegoIssueResult,
    allowance_number,
    amego_order_id,
    build_allowance_query_data,
    build_f0401_data,
    build_f0501_data,
    build_g0401_data,
    build_invoice_query_by_number_data,
    build_invoice_query_data,
    parse_f0401_success,
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
from app.shared.enums import (
    EInvoiceAction,
    EInvoiceMessageType,
    InvoiceStatus,
    InvoiceType,
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
)

# 回執種類（einvoice_result_events.result_kind）。
RESULT_KIND_PROCESS = "PROCESS"
RESULT_KIND_SUMMARY = "SUMMARY"

# 發票開立日以台灣時區呈現（Amego invoice_time 為 Unix 秒；折讓日亦同）。
_TAIPEI_TZ = ZoneInfo("Asia/Taipei")


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

    async def void_invoice_for_sale(self, store_id: int, sale_id: int) -> Invoice | None:
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
        if invoice.status in (InvoiceStatus.VOID, InvoiceStatus.VOID_PENDING):
            return invoice
        if invoice.status is InvoiceStatus.ISSUED:
            invoice.status = InvoiceStatus.VOID_PENDING
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
        await self._session.flush()
        return invoice

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
        invoice = await self._repo.get_invoice(store_id, invoice_id)
        if invoice is None:
            raise InvoiceNotFound(f"發票不存在或不屬於本店：id={invoice_id}")
        if invoice.status is not InvoiceStatus.ISSUED:
            raise InvoiceNotIssued(
                f"發票 {invoice_id} 尚未開立（狀態 {invoice.status.value}），不可折讓"
            )
        if return_id is not None:
            existing = await self._repo.find_allowance_by_return(store_id, return_id)
            if existing is not None:
                raise DuplicateAllowanceForReturn(f"退貨單 {return_id} 已有折讓，不可重複開立")

        prior = await self._repo.sum_allowances_total(store_id, invoice_id)
        if prior + total > invoice.total:
            raise AllowanceExceedsInvoice(
                f"折讓累計 {prior + total} 超過原發票總額 {invoice.total}"
            )

        net, tax = split_tax_inclusive(total, Decimal(invoice.tax_rate))
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
                f"佇列項目非 PENDING（目前 {item.status.value}），不可拋檔"
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
        self, store_id: int, queue_id: int, *, client: AmegoClient
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
                f"佇列項目非 PENDING（目前 {item.status.value}），不可上送"
            )
        endpoint = self._AMEGO_ENDPOINTS.get(item.message_type)
        if endpoint is None:
            raise EInvoiceQueueNotDroppable(
                f"訊息類型 {item.message_type.value} 不支援 Amego 上送"
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
        frozen = locked.amego_payload
        if (
            frozen is None
            or hashlib.sha256(frozen.encode("utf-8")).hexdigest() != locked.xml_sha256
        ):
            await self._session.commit()
            raise EInvoiceDropError(
                f"佇列 {queue_id} 認領 payload 遺失或與 sha 不符，拒絕重送（需人工對帳）"
            )
        payload = json.loads(frozen)

        # **一律對帳先行**（Codex 第三/七輪）：本地「是否已認領」快照可能過期——同列的另一
        # 呼叫可先插隊送出且結果未知（PENDING 依舊）；認領一旦 commit，平台就可能收過這則
        # 訊息。每次上送前先查平台實態：已套用 → 補記成功、絕不重送；**明確未套用**才送
        # （曖昧查詢回應由解析層拋 AmegoTransportError 擋下）。多一次查詢換確定性（單店）。
        if locked.action is EInvoiceAction.ISSUE and sale_id is not None:
            order_id = amego_order_id(store_id=store_id, sale_id=sale_id)
            query_resp = await client.call(
                "/json/invoice_query", build_invoice_query_data(order_id=order_id)
            )
            recovered = parse_query_issued(query_resp)
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
                query_resp = await client.call(
                    "/json/invoice_query",
                    build_invoice_query_by_number_data(invoice_number=void_target.invoice_no),
                )
                if parse_query_invoice_voided(query_resp):
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
            query_resp = await client.call(
                "/json/allowance_query",
                build_allowance_query_data(
                    number=allowance_number(
                        store_id=store_id, allowance_id=locked.allowance_id
                    )
                ),
            )
            if parse_query_allowance_exists(query_resp):
                return await self._record_amego_outcome(
                    store_id,
                    queue_id,
                    success=True,
                    status_code="0",
                    message="以 allowance_query 對帳確認平台已有折讓（前次結果未知）",
                    delivery_attempt=claim_attempts,
                    issue_result=None,
                )

        resp = await client.call(endpoint, payload)  # AmegoTransportError → 維持已認領 PENDING
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
        return await self._record_amego_outcome(
            store_id,
            queue_id,
            success=success,
            status_code=str(code),
            message=str(resp.get("msg") or "")[:500] or None,
            delivery_attempt=claim_attempts,
            issue_result=issue_result,
        )

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

    async def issue_for_sale(
        self, store_id: int, sale_id: int, *, client: AmegoClient
    ) -> Invoice:
        """POS 結帳後開立入口：把該銷售的發票上送 Amego，回開立後發票（冪等）。

        已 ISSUED → 直接回（POS 重試/重印取號用）；PENDING → 送其 F0401 佇列列
        （FAILED 列先 retry 轉回 PENDING 再送，POS 一鍵重試）；其他狀態（作廢中/已作廢）
        → EInvoiceQueueNotDroppable。平台明確拒絕 → AmegoIssueFailed（佇列已 FAILED、
        留 last_error）；傳輸中斷 → AmegoTransportError（已認領，之後對帳）。
        """
        invoice = await self._repo.find_invoice_by_sale(store_id, sale_id)
        if invoice is None:
            raise InvoiceNotFound(f"銷售 {sale_id} 無發票（einvoice 未啟用或非本店）")
        if invoice.status is InvoiceStatus.ISSUED:
            return invoice
        if invoice.status is not InvoiceStatus.PENDING:
            raise EInvoiceQueueNotDroppable(
                f"發票狀態 {invoice.status.value}，不可開立"
            )
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
        sent = await self.send_via_amego(store_id, issue_item.id, client=client)
        if sent.status is not UploadStatus.UPLOADED:
            raise AmegoIssueFailed(
                f"Amego 拒絕開立：{sent.last_error or '未知錯誤'}（可稍後重試）"
            )
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
            return build_g0401_data(
                number=allowance_number(store_id=store_id, allowance_id=allowance.id),
                allowance_date=datetime.now(UTC).astimezone(_TAIPEI_TZ).date(),
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
            raise EInvoiceQueueNotRetryable(f"僅 FAILED 可重送，目前狀態：{item.status.value}")
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
            # 退貨觸發的作廢（sale 未被 void、仍 PENDING_ISSUE）→ 收斂為 NOT_ISSUED（無有效發票）。
            # sale-void 路徑的 invoice_status 已是 VOID（void_sale 設）→ 此呼叫為 no-op。
            await SalesService(self._session).mark_invoice_not_issued(store_id, invoice.sale_id)

    async def _apply_failure_transition(self, store_id: int, item: EInvoiceUploadQueue) -> None:
        """ProcessResult 失敗時的狀態收斂：作廢請求中的 F0401 失敗 → 平台未開立 → 正式 VOID。"""
        if item.action is not EInvoiceAction.ISSUE or item.invoice_id is None:
            return
        invoice = await self._repo.get_invoice(store_id, item.invoice_id)
        if invoice is not None and invoice.status is InvoiceStatus.VOID_PENDING:
            invoice.status = InvoiceStatus.VOID
            # 同上：退貨觸發（sale 仍 PENDING_ISSUE）→ NOT_ISSUED；sale-void → no-op。
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
