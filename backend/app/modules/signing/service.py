"""signing 業務邏輯：任務狀態機（PENDING→SIGNED/CANCELLED）、簽名守衛、切結書版本落庫。

- 簽名綁定內容快照：content 於建立時凍結；內容要變＝作廢重推重簽（docs/23 §3）。
- 簽名影像守衛：base64 → PNG magic bytes → 大小上限，擋非影像垃圾與超大 payload。
- chosen_payout：AFFIDAVIT 必選且限 CASH/STORE_CREDIT（D7）；其他種類不得帶。
"""

import base64
import binascii
import zlib
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.signing import agreements
from app.modules.signing.models import AgreementVersion, SignatureTask
from app.modules.signing.repository import SigningRepository
from app.modules.signing.schemas import (
    MAX_SIGNATURE_B64_CHARS,
    MAX_SIGNATURE_BYTES,
    SignatureTaskCreate,
)
from app.shared.enums import PayoutMethod, SignatureTaskKind, SignatureTaskStatus
from app.shared.exceptions import (
    ContactNotFound,
    InvalidKioskPayout,
    InvalidSignatureImage,
    SignatureTaskConflict,
    SignatureTaskNotFound,
    SignatureTaskNotPending,
)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MAX_SIGNATURE_DIMENSION = 4096  # 簽名 canvas 尺寸上限（像素）


class SigningService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = SigningRepository(session)

    async def create_task(
        self, store_id: int, data: SignatureTaskCreate, *, created_by: int
    ) -> SignatureTask:
        """建立簽署任務（店員發起）。AFFIDAVIT 自動綁定當前切結書版本（lazy 落庫）。"""
        # 跨模組只經對方 service：確認任務對象存在且屬同店。
        from app.modules.contacts.service import ContactService

        contact = await ContactService(self._session).get_contact(store_id, data.contact_id)
        if contact is None:
            raise ContactNotFound(f"contact {data.contact_id} 不存在或不屬本店")

        agreement_version_id: int | None = None
        if data.kind is SignatureTaskKind.ACQUISITION_AFFIDAVIT:
            agreement_version_id = (await self._get_or_seed_current_agreement()).id

        # 重推＝舊單作廢：同店同時最多一件待簽（DB 部分唯一索引為最終防線）。
        # 否則店員改內容重推後，停在舊頁面的平板仍可簽下舊快照，留下錯誤證據。
        await self._repo.cancel_pending_tasks(store_id)

        task = SignatureTask(
            store_id=store_id,
            kind=data.kind,
            contact_id=data.contact_id,
            content=data.content,
            agreement_version_id=agreement_version_id,
            ref_type=data.ref_type,
            ref_id=data.ref_id,
            created_by=created_by,
        )
        try:
            return await self._repo.add(task)
        except IntegrityError as exc:  # 併發重推：另一筆先建成功（單一待簽唯一索引）
            raise SignatureTaskConflict("簽署任務建立衝突（另一筆同時建立），請重試") from exc

    async def cancel_task(self, store_id: int, task_id: int) -> SignatureTask:
        """作廢任務（店員端；客人反悔/內容要改都走這裡再重推）。僅 PENDING 可作廢。"""
        task = await self._repo.get_for_update(store_id, task_id)
        if task is None:
            raise SignatureTaskNotFound(f"簽署任務 {task_id} 不存在")
        if task.status is not SignatureTaskStatus.PENDING:
            raise SignatureTaskNotPending(
                f"簽署任務 {task_id} 非待簽狀態（{task.status}），不可作廢"
            )
        task.status = SignatureTaskStatus.CANCELLED
        task.cancelled_at = datetime.now(UTC)
        await self._session.flush()
        await self._session.refresh(task)
        return task

    async def sign_task(
        self,
        store_id: int,
        task_id: int,
        *,
        signature_image_base64: str,
        chosen_payout: PayoutMethod | None,
    ) -> SignatureTask:
        """手持端送出簽名：驗 PNG、驗撥款選擇（D7）、PENDING→SIGNED（FOR UPDATE 序列化）。"""
        image = self._decode_signature(signature_image_base64)
        task = await self._repo.get_for_update(store_id, task_id)
        if task is None:
            raise SignatureTaskNotFound(f"簽署任務 {task_id} 不存在")
        if task.status is not SignatureTaskStatus.PENDING:
            raise SignatureTaskNotPending(
                f"簽署任務 {task_id} 非待簽狀態（{task.status}），請店員重新推送"
            )
        if task.kind is SignatureTaskKind.ACQUISITION_AFFIDAVIT:
            if chosen_payout not in (PayoutMethod.CASH, PayoutMethod.STORE_CREDIT):
                raise InvalidKioskPayout("收購撥款須於現金/購物金中二選一（docs/23 D7）")
        elif chosen_payout is not None:
            raise InvalidKioskPayout("此簽署任務不涉及撥款選擇，不可帶 chosen_payout")

        task.signature_image = image
        task.signed_at = datetime.now(UTC)
        task.chosen_payout = chosen_payout
        task.status = SignatureTaskStatus.SIGNED
        await self._session.flush()
        await self._session.refresh(task)
        return task

    async def get_task(self, store_id: int, task_id: int) -> SignatureTask | None:
        return await self._repo.get(store_id, task_id)

    async def get_pending_task_for_kiosk(self, store_id: int, task_id: int) -> SignatureTask | None:
        """手持端重讀：僅回 PENDING 中的任務，其餘一律 None（→404）。

        手持裝置在客人手上，不得憑 ID 枚舉歷史簽署單的內容快照；簽名送出的
        回應已帶最終狀態，事後不需要（也不允許）再讀。
        """
        task = await self._repo.get(store_id, task_id)
        if task is None or task.status is not SignatureTaskStatus.PENDING:
            return None
        return task

    async def latest_pending_task(self, store_id: int) -> SignatureTask | None:
        """手持端輪詢：最新一筆待簽任務（單店單裝置，同時最多一件在簽）。"""
        return await self._repo.latest_pending(store_id)

    async def list_tasks(
        self,
        store_id: int,
        status: SignatureTaskStatus | None,
        *,
        limit: int,
        offset: int,
    ) -> list[SignatureTask]:
        return await self._repo.list_tasks(store_id, status, limit=limit, offset=offset)

    async def get_agreement_for_task(self, task: SignatureTask) -> AgreementVersion | None:
        """取任務綁定的切結書版本列（手持端顯示全文用）。"""
        if task.agreement_version_id is None:
            return None
        return await self._repo.get_agreement_by_id(task.agreement_version_id)

    async def _get_or_seed_current_agreement(self) -> AgreementVersion:
        """取當前切結書版本；首次使用時自 agreements.AGREEMENT_TEXTS lazy 落庫。

        版本列不可變：改版＝AGREEMENT_TEXTS 加新條目→此處落新列，舊簽名仍指舊列。
        單店單機、無並發種子競態（DB 唯一約束 uq_agreement_versions_version 為最終防線）。
        """
        version = agreements.CURRENT_AGREEMENT_VERSION
        existing = await self._repo.get_agreement_by_version(version)
        if existing is not None:
            return existing
        title, body = agreements.AGREEMENT_TEXTS[version]
        try:
            return await self._repo.add_agreement(
                AgreementVersion(version=version, title=title, body=body)
            )
        except IntegrityError as exc:  # 首次落庫競態：另一筆先種成功
            raise SignatureTaskConflict("切結書版本初始化衝突，請重試") from exc

    @staticmethod
    def _decode_signature(signature_image_base64: str) -> bytes:
        """base64 → bytes；長度上限於解碼前先擋，再做完整 PNG 結構驗證。

        簽名是法律證據，存進去就必須能渲染/列印：逐 chunk 驗長度與 CRC、IHDR 居首
        且尺寸合理、至少一個 IDAT、IEND 為零長度終結且無尾隨資料。schema 已有
        max_length，此處為最後防線；不合格一律 InvalidSignatureImage。
        """
        if len(signature_image_base64) > MAX_SIGNATURE_B64_CHARS:
            raise InvalidSignatureImage(f"簽名影像過大（上限 {MAX_SIGNATURE_BYTES // 1000} KB）")
        try:
            image = base64.b64decode(signature_image_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise InvalidSignatureImage("簽名影像非有效 base64") from exc
        if len(image) > MAX_SIGNATURE_BYTES:
            raise InvalidSignatureImage(f"簽名影像過大（上限 {MAX_SIGNATURE_BYTES // 1000} KB）")
        if not image.startswith(_PNG_MAGIC):
            raise InvalidSignatureImage("簽名影像必須為 PNG 格式")
        SigningService._validate_png_chunks(image)
        width = int.from_bytes(image[16:20], "big")
        height = int.from_bytes(image[20:24], "big")
        if not (1 <= width <= _MAX_SIGNATURE_DIMENSION and 1 <= height <= _MAX_SIGNATURE_DIMENSION):
            raise InvalidSignatureImage("簽名影像尺寸不合理")
        return image

    @staticmethod
    def _validate_png_chunks(image: bytes) -> None:
        """逐 chunk 掃描 PNG 結構；任何缺陷一律 InvalidSignatureImage。

        驗證：每個 chunk 的長度落在檔內、CRC 正確；IHDR 為首個 chunk（長度 13）；
        至少一個 IDAT；IEND 為零長度且為檔案最後一個 chunk（無尾隨資料）。
        """
        malformed = InvalidSignatureImage("簽名影像 PNG 結構不完整或損毀")
        pos = 8  # magic 之後
        first = True
        seen_idat = False
        while True:
            if pos + 12 > len(image):  # 至少要放得下 length+type+CRC
                raise malformed
            length = int.from_bytes(image[pos : pos + 4], "big")
            chunk_type = image[pos + 4 : pos + 8]
            data_end = pos + 8 + length
            if length > len(image) - pos - 12:
                raise malformed
            crc = int.from_bytes(image[data_end : data_end + 4], "big")
            if zlib.crc32(image[pos + 4 : data_end]) != crc:
                raise malformed
            if first:
                if chunk_type != b"IHDR" or length != 13:
                    raise malformed
                first = False
            if chunk_type == b"IDAT":
                seen_idat = True
            if chunk_type == b"IEND":
                if length != 0 or data_end + 4 != len(image) or not seen_idat:
                    raise malformed
                return
            pos = data_end + 4
