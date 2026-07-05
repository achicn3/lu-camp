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
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import split_tax_inclusive
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
        tax_rate: Decimal,
        return_id: int | None = None,
    ) -> InvoiceAllowance:
        """開立折讓單並排入 G0401 上傳佇列（退貨且原發票已開立，§7 不變量 5）。

        守衛（F6）：原發票必須已開立（ISSUED，否則 InvoiceNotIssued）；同一退貨至多一張折讓
        （return_id 唯一，否則 DuplicateAllowanceForReturn）；累計折讓不得超過原發票總額
        （否則 AllowanceExceedsInvoice）。
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

        net, tax = split_tax_inclusive(total, tax_rate)
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

    async def retry(self, store_id: int, queue_id: int) -> EInvoiceUploadQueue:
        """把 FAILED 佇列列轉回 PENDING（attempts+1），供重新拋檔/上傳。

        不觸碰發票與字軌號碼——重送絕不為同一筆銷售產生第二個發票號碼（不變量 2）。
        清掉上次拋檔痕跡（xml_path/sha256/dropped_at），使可重新拋檔。
        """
        item = await self._repo.lock_queue_item(store_id, queue_id)
        if item is None:
            raise EInvoiceQueueItemNotFound(f"佇列項目不存在或不屬於本店：id={queue_id}")
        if item.status is not UploadStatus.FAILED:
            raise EInvoiceQueueNotRetryable(f"僅 FAILED 可重送，目前狀態：{item.status.value}")
        item.status = UploadStatus.PENDING
        item.attempts += 1
        item.last_error = None
        item.xml_path = None
        item.xml_sha256 = None
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
        item = await self._repo.lock_queue_item(store_id, queue_id)
        if item is None:
            raise EInvoiceQueueItemNotFound(f"佇列項目不存在或不屬於本店：id={queue_id}")

        await self._repo.add_result_event(
            EInvoiceResultEvent(
                store_id=store_id,
                queue_id=queue_id,
                result_kind=kind,
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
        others = await self._repo.count_other_pending_allowance_items(
            store_id, invoice.id, exclude_queue_id=item.id
        )
        if others > 0:
            return  # 其他折讓仍在途，sale 級狀態維持 PENDING_ALLOWANCE
        from app.modules.sales.service import SalesService  # 函式內 import 破 sales↔einvoice 循環

        await SalesService(self._session).mark_invoice_allowance(store_id, invoice.sale_id)

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
