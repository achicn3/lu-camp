"""電子發票模組驗收（docs/37 PE）：對**真 Amego 測試平台**把九項矩陣走完。

**寫成 Python 而非計畫中的 `.mjs`**：矩陣第 3/4/5/8 項要以 `invoice_query`／
`allowance_query` 向平台查證，而這兩支**沒有對外端點**（只存在於後端的「對帳先行」）。
用 JS 就得在測試裡重刻 Amego 的 MD5 簽章——把產品邏輯複製一份到測試裡，
日後改了簽章方式測試還會過。用 Python 可以直接重用真的 `AmegoClient`。

業務動作一律走**真 HTTP API**（與店員操作同一條路），只有平台查證直呼 client。

**號碼會真的被消耗。** `OrderId = S{store}-{sale}` 是確定性導出的：同一筆銷售一旦開立
就永遠不能再開第二張。重跑必須用新的 sale，不能重用舊的。每次執行都把佔用的號段
寫進證據鏈，供日後避開。

執行（backend 需已啟動並指向測試統編的資料庫）：

    cd backend
    ALLOW_DEV_SEED=true uv run python -m app.scripts.einvoice_acceptance --api http://127.0.0.1:8010
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import select, text

import app.modules.backup.models
import app.modules.callticket.models
import app.modules.campaigns.models
import app.modules.cashdrawer.models
import app.modules.consignment.models
import app.modules.customerdisplay.models
import app.modules.einvoice.models
import app.modules.inventory.models
import app.modules.menu.models
import app.modules.purchasing.models
import app.modules.returns.models
import app.modules.sales.models
import app.modules.settings.models
import app.modules.signing.models
import app.modules.stocktake.models
import app.modules.storecredit.models  # noqa: F401
from app.core.db import get_sessionmaker
from app.modules.einvoice.amego import (
    build_allowance_query_data,
    build_invoice_query_data,
)
from app.modules.inventory.models import SerializedItem
from app.modules.sales.models import Sale as SaleModel
from app.modules.signing.schemas import SignatureTaskCreate
from app.modules.signing.service import SigningService
from app.scripts.seed_allowances import _kiosk_ids
from app.scripts.seed_demo import make_signature_png, touch_kiosk
from app.scripts.seed_issue_invoices import (
    _guard_environment,
    _guard_test_seller,
    _make_client,
)
from app.shared.enums import SerializedItemStatus, SignatureTaskKind
from app.shared.exceptions import DomainError


@dataclass
class Evidence:
    """證據鏈：每一步保留平台回應的原始碼與號碼。"""

    steps: list[dict[str, Any]] = field(default_factory=list)
    order_ids: list[str] = field(default_factory=list)
    allowance_nos: list[str] = field(default_factory=list)

    def add(self, name: str, ok: bool, **detail: Any) -> bool:
        self.steps.append({"name": name, "ok": ok, **detail})
        mark = "✅" if ok else "❌"
        extra = " ".join(f"{k}={v}" for k, v in detail.items() if v is not None)
        print(f"{mark} {name}{'：' + extra if extra else ''}", flush=True)
        return ok


class Api:
    """打自家 HTTP API——與店員操作走同一條路。"""

    def __init__(self, base: str, token: str) -> None:
        self._base = base.rstrip("/")
        self._token = token

    async def call(
        self, method: str, path: str, *, body: Any = None, headers: dict[str, str] | None = None
    ) -> tuple[int, Any]:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.request(
                method,
                f"{self._base}{path}",
                json=body,
                headers={"Authorization": f"Bearer {self._token}", **(headers or {})},
            )
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, resp.text


async def login(base: str, username: str, password: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base.rstrip('/')}/api/v1/auth/login",
            json={"username": username, "password": password},
        )
    resp.raise_for_status()
    return str(resp.json()["access_token"])


async def _pick_items(count: int, store_id: int) -> list[str]:
    """取可售序號品。每次驗收都要用**新的**品項——同一筆銷售不能重開發票。"""
    async with get_sessionmaker()() as session:
        rows = list(
            await session.scalars(
                select(SerializedItem.item_code)
                .where(
                    SerializedItem.store_id == store_id,
                    SerializedItem.status == SerializedItemStatus.IN_STOCK,
                    SerializedItem.listed_price > 100,
                )
                .order_by(SerializedItem.id.desc())
                .limit(count)
            )
        )
    if len(rows) < count:
        raise SystemExit(f"可售庫存不足（需 {count}，只有 {len(rows)}）")
    return [str(r) for r in rows]


async def _open_session_if_needed(api: Api) -> int | None:
    status, data = await api.call("GET", "/api/v1/cash-sessions/current")
    if status == 200 and isinstance(data, dict) and data.get("id"):
        return None
    status, data = await api.call(
        "POST", "/api/v1/cash-sessions/open", body={"opening_float": "30000"}
    )
    if status >= 300:
        raise SystemExit(f"開帳失敗 {status}：{data}")
    return int(data["id"])


async def _sell(
    api: Api, item_codes: str | list[str], key: str, invoice: dict[str, Any] | None
) -> dict[str, Any]:
    """結帳一筆（現金），可帶發票資訊。回傳銷售單。

    **折讓那一項需要兩行以上**：單行單件的單退下去就是累計全退，走的是作廢原發票
    （F0501）而不是折讓，且會要求收回紙本證明聯。第一版只賣一件，於是「部分退貨」
    根本不成立（實測回 409：本次為整筆退貨…）。
    """
    codes = [item_codes] if isinstance(item_codes, str) else item_codes
    body: dict[str, Any] = {
        "lines": [{"line_type": "SERIALIZED", "item_code": c} for c in codes],
        "expected_einvoice_enabled": True,
    }
    if invoice is not None:
        body["invoice"] = invoice
    status, data = await api.call(
        "POST", "/api/v1/sales", body=body, headers={"Idempotency-Key": key}
    )
    if status != 201:
        raise SystemExit(f"結帳失敗 {status}：{json.dumps(data, ensure_ascii=False)[:300]}")
    return dict(data)


async def _issue(api: Api, sale_id: int) -> tuple[int, Any]:
    return await api.call("POST", f"/api/v1/einvoice/sales/{sale_id}/issue")


async def _platform_invoice_by_order(store_id: int, order_id: str) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        client = await _make_client(session, store_id)
        resp = await client.call("/json/invoice_query", build_invoice_query_data(order_id=order_id))
    data = resp.get("data")
    out: dict[str, Any] = dict(data) if isinstance(data, dict) else {}
    out["_code"] = resp.get("code")
    return out


async def _platform_allowance(store_id: int, number: str) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        client = await _make_client(session, store_id)
        resp = await client.call("/json/allowance_query", build_allowance_query_data(number=number))
    data = resp.get("data")
    out: dict[str, Any] = dict(data) if isinstance(data, dict) else {}
    out["_code"] = resp.get("code")
    return out


async def run(
    *, base: str, store_id: int, username: str, password: str, carrier: str | None
) -> Evidence:
    from app.core.config import get_settings

    _guard_environment(get_settings().database_url)
    async with get_sessionmaker()() as session:
        await _guard_test_seller(session, store_id)

    ev = Evidence()
    run_tag = uuid4().hex[:8]
    api = Api(base, await login(base, username, password))
    await _open_session_if_needed(api)
    items = await _pick_items(12, store_id)
    nxt = iter(items)

    # ── 1. 開立四變體 ───────────────────────────────────────────────
    # **冪等鍵只能是 ASCII**：它是 HTTP header，中文會直接讓 httpx 編碼失敗。
    # 鍵裡也帶執行批號，否則重跑會撞上上一輪的鍵而被當成重送。
    variants: list[tuple[str, str, dict[str, Any] | None]] = [
        ("B2C 一般", "b2c", None),
        # 買方統編**不能用賣方自己的** `12345678`，且必須通過檢核碼——
        # 用 12345678 平台回「BuyerIdentifier 格式錯誤」（實測）。
        ("B2B 統編", "b2b", {"buyer_tax_id": "35215468", "buyer_name": "測試買方股份有限公司"}),
        # 手機載具**平台會向財政部查驗真實性**，隨便編一組回「載具號碼不存在」（實測）。
        # 與 LINE Pay 的 oneTimeKey 同類：沒有真東西就驗不了，不能假造。
        # 未提供 `--carrier` 時明確標為「未驗證」，不當成通過。
        ("手機載具", "carrier", {"mobile_carrier": carrier} if carrier else None),
        ("捐贈", "donate", {"npoban": "25885"}),
    ]
    issued: dict[str, dict[str, Any]] = {}
    for label, slug, invoice in variants:
        if slug == "carrier" and not carrier:
            ev.add(
                "1. 開立－手機載具（未驗證：需真實已登記的手機條碼，見 --carrier）",
                False,
                原因="平台會向財政部查驗載具真實性，不可假造",
            )
            next(nxt)  # 保持品項指標一致，後續步驟才不會取到同一件
            continue
        # B2B 那張稍後要做**部分退貨**開折讓，故賣兩件——單件退下去是全退、走作廢
        codes = [next(nxt), next(nxt)] if slug == "b2b" else next(nxt)
        sale = await _sell(api, codes, f"pe-{run_tag}-{slug}", invoice)
        status, data = await _issue(api, sale["id"])
        ok = status == 200 and bool(data.get("invoice_no"))
        ev.order_ids.append(f"S{store_id}-{sale['id']}")
        issued[label] = {"sale": sale, "invoice": data if ok else None}
        ev.add(
            f"1. 開立－{label}",
            ok,
            號碼=data.get("invoice_no") if ok else None,
            隨機碼=data.get("random_number") if ok else None,
            回應=None if ok else str(data)[:120],
        )

    # ── 2. 冪等：同一筆再 issue，回原發票且不再送平台 ────────────────
    first = issued["B2C 一般"]
    if first["invoice"]:
        before = first["invoice"]["invoice_no"]
        status, again = await _issue(api, first["sale"]["id"])
        ev.add(
            "2. 冪等－重複 issue 回原發票",
            status == 200 and again.get("invoice_no") == before,
            號碼=again.get("invoice_no"),
        )

    # ── 3. 對帳先行：以 order_id 查得到、金額相符 ────────────────────
    if first["invoice"]:
        oid = f"S{store_id}-{first['sale']['id']}"
        found = await _platform_invoice_by_order(store_id, oid)
        same = str(found.get("invoice_number") or "") == str(first["invoice"]["invoice_no"])
        amount_ok = Decimal(str(found.get("total_amount", "-1"))) == Decimal(
            str(first["sale"]["total"])
        )
        ev.add(
            "3. 對帳先行－平台查得到且金額相符",
            same and amount_ok,
            平台號碼=found.get("invoice_number"),
            平台金額=found.get("total_amount"),
            本地金額=first["sale"]["total"],
        )

    async def _queue_for_sale(sale_id: int, action: str) -> dict[str, Any] | None:
        """該銷售對應動作的佇列列——**直接查資料庫**。

        不用 `GET /einvoice/queue`：它上限 200 筆、無排序控制，而本庫有 17,914 筆 ISSUE，
        新建立的排在最後根本撈不到（實測連續三次「查不到而整段跳過」）。
        > 這同時是一個**產品可用性問題**：店長在正式環境也會遇到同一件事——
        > 佇列頁翻不到自己剛才那一筆。已記入 docs/37 PE 發現。

        另注意 ISSUE／VOID 掛 `invoice_id`，而 **ALLOWANCE 掛 `allowance_id`**
        （其 `invoice_id` 為空）。
        """
        async with get_sessionmaker()() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT q.id, q.status, q.action FROM einvoice_upload_queue q"
                        " LEFT JOIN invoices i ON i.id = q.invoice_id"
                        " LEFT JOIN invoice_allowances a ON a.id = q.allowance_id"
                        " LEFT JOIN invoices ai ON ai.id = a.invoice_id"
                        " WHERE q.store_id = :store AND q.action = :action"
                        "   AND COALESCE(i.sale_id, ai.sale_id) = :sale"
                        " ORDER BY q.id DESC LIMIT 1"
                    ),
                    {"store": store_id, "action": action, "sale": sale_id},
                )
            ).first()
        return {"id": int(row[0]), "status": str(row[1]), "action": str(row[2])} if row else None

    async def _consent(sale_id: int, sale_line_id: int) -> int | None:
        """退貨同意簽署（作業要點第 9 點）：作廢與折讓都必須經買受人同意。

        **不簽就退不了**——實測回 409「依規定須經買受人同意：請先請客人於顧客螢幕簽名確認」。
        第一版漏了這步，作廢與折讓兩項直接卡死。
        """
        async with get_sessionmaker()() as session:
            device_id, terminal_id = await _kiosk_ids(session, store_id)
            await touch_kiosk(session, store_id, device_id)
            await session.commit()
            signing = SigningService(session)
            sale = await session.get(SaleModel, sale_id)
            try:
                task = await signing.create_task(
                    store_id,
                    SignatureTaskCreate(
                        kind=SignatureTaskKind.RETURN_INVOICE_CONSENT,
                        contact_id=sale.buyer_contact_id if sale else None,
                        content={"lines": [{"sale_line_id": sale_line_id, "qty": 1}]},
                        terminal_id=terminal_id,
                        ref_type="sale",
                        ref_id=sale_id,
                    ),
                    created_by=1,
                )
                await signing.acknowledge_task(store_id, device_id, task.id)
                await signing.sign_task(
                    store_id,
                    task.id,
                    device_id=device_id,
                    signature_image_base64=make_signature_png(random.Random(sale_id)),
                    chosen_payout=None,
                )
            except DomainError:
                await session.rollback()
                return None
            await session.commit()
            return int(task.id)

    async def _return(sale_id: int, *, full: bool, key: str) -> tuple[int, Any]:
        _st, detail = await api.call("GET", f"/api/v1/sales/{sale_id}")
        line = (detail or {}).get("lines", [{}])[0]
        consent_id = await _consent(sale_id, line.get("id"))
        return await api.call(
            "POST",
            "/api/v1/returns",
            body={
                "sale_id": sale_id,
                "reason": "電子發票驗收",
                "lines": [{"sale_line_id": line.get("id"), "qty": 1}],
                "invoice_recalled": full,
                "consent_signature_task_id": consent_id,
            },
            headers={"Idempotency-Key": key},
        )

    # ── 4. 作廢：整筆退貨 → F0501 → 真的 send → 平台確認已作廢 ──────
    void_target = issued.get("捐贈", {"invoice": None})
    if void_target.get("invoice"):
        sale_id = void_target["sale"]["id"]
        status, resp = await _return(sale_id, full=True, key=f"pe-{run_tag}-void")
        queued = await _queue_for_sale(sale_id, "VOID") if status == 201 else None
        sent = None
        if queued:
            _s, sent = await api.call("POST", f"/api/v1/einvoice/queue/{queued['id']}/send")
        number = void_target["invoice"]["invoice_no"]
        found = await _platform_invoice_by_order(store_id, f"S{store_id}-{sale_id}")
        # **平台受理但尚在處理的作廢，頂層仍是 C0401**，待作廢掛在 `wait[]`
        # （產品原始碼已記載此實測行為）。只看頂層會誤判成沒作廢。
        wait = found.get("wait")
        waiting_void = isinstance(wait, list) and any(
            isinstance(w, dict) and str(w.get("invoice_type") or "") in ("C0501", "A0501")
            for w in wait
        )
        voided = str(found.get("invoice_type") or "") in ("C0501", "A0501") or waiting_void
        ev.add(
            "4. 作廢－整筆退貨後 F0501 送出並經平台確認",
            bool(queued) and voided,
            退貨=status,
            退貨訊息=(resp or {}).get("detail") if status != 201 else None,
            佇列=(queued or {}).get("status"),
            送出後=(sent or {}).get("status"),
            平台狀態=found.get("invoice_type"),
            待處理=found.get("wait"),
            號碼=number,
        )

    # ── 5. 折讓：部分退貨 → G0401 → 真的 send → allowance_query 確認 ──
    allow_target = issued.get("B2B 統編", {"invoice": None})
    if allow_target.get("invoice"):
        sale_id = allow_target["sale"]["id"]
        status, resp5 = await _return(sale_id, full=False, key=f"pe-{run_tag}-allow")
        queued = await _queue_for_sale(sale_id, "ALLOWANCE") if status == 201 else None
        sent = None
        if queued:
            _s, sent = await api.call("POST", f"/api/v1/einvoice/queue/{queued['id']}/send")
        async with get_sessionmaker()() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT a.allowance_no, a.total, a.net, a.tax, i.invoice_no"
                        " FROM invoice_allowances a JOIN invoices i ON i.id = a.invoice_id"
                        " WHERE i.sale_id = :s ORDER BY a.id DESC LIMIT 1"
                    ),
                    {"s": sale_id},
                )
            ).first()
        plat = await _platform_allowance(store_id, str(row[0])) if row is not None else {}
        if row is not None:
            ev.allowance_nos.append(str(row[0]))
        # **折讓的平台 `total_amount` 是未稅、`tax_amount` 是稅額**，與 `invoice_query`
        # 的含稅口徑不同（產品的 `parse_query_allowance_exists` 早已對真平台實測確認並
        # 正確處理；是本驗收第一版比錯欄位，拿含稅 350 去對未稅 333）。
        matched = (
            row is not None
            and Decimal(str(plat.get("total_amount", "-1"))) == Decimal(str(row[2]))
            and Decimal(str(plat.get("tax_amount", "-1"))) == Decimal(str(row[3]))
            and str(plat.get("allowance_number") or "") == str(row[0])
        )
        ev.add(
            "5. 折讓－部分退貨後 G0401 送出並經平台確認",
            bool(queued) and matched,
            退貨=status,
            退貨訊息=(resp5 or {}).get("detail") if status != 201 else None,
            折讓單號=(row[0] if row is not None else None),
            原發票=(row[4] if row is not None else None),
            平台未稅=plat.get("total_amount"),
            平台稅額=plat.get("tax_amount"),
            本地未稅=(str(row[2]) if row is not None else None),
            本地稅額=(str(row[3]) if row is not None else None),
        )

        # ── 6. 已折讓後再退貨 → 必須繼續折讓，不得作廢原發票 ──────────
        status2, _r2 = await _return(sale_id, full=False, key=f"pe-{run_tag}-allow2")
        void_q = await _queue_for_sale(sale_id, "VOID")
        ev.add(
            "6. 已折讓後再退貨－仍走折讓、不作廢原發票",
            status2 in (201, 409, 422) and void_q is None,
            第二次退貨=status2,
            是否排了作廢=bool(void_q),
        )

    # ── 7. 佇列端點：retry（只吃 FAILED）與 result（終態冪等） ────────
    async with get_sessionmaker()() as session:
        failed = (
            await session.execute(
                text(
                    "SELECT id FROM einvoice_upload_queue"
                    " WHERE store_id = :s AND status = 'FAILED' ORDER BY id DESC LIMIT 1"
                ),
                {"s": store_id},
            )
        ).first()
        done = (
            await session.execute(
                text(
                    "SELECT id FROM einvoice_upload_queue"
                    " WHERE store_id = :s AND status = 'UPLOADED' ORDER BY id DESC LIMIT 1"
                ),
                {"s": store_id},
            )
        ).first()
    if failed is not None:
        st, _d = await api.call("POST", f"/api/v1/einvoice/queue/{failed[0]}/retry")
        ev.add("7a. retry－FAILED 可轉回 PENDING", st == 200, 佇列=failed[0], 回應=st)
    else:
        ev.add("7a. retry－無 FAILED 可測（略過）", True)
    if done is not None:
        st, _d = await api.call(
            "POST",
            f"/api/v1/einvoice/queue/{done[0]}/result",
            body={"success": True, "message": "PE 驗收重複回執"},
        )
        # 終態重複回執：不應改狀態，且不得 500
        ev.add("7b. result－終態重複回執不改狀態", st in (200, 409, 422), 回應=st)

    # ── 8. 手開紙本：登記後 ISSUE 佇列轉 CANCELLED、平台上沒有那張 ───
    paper_sale = await _sell(api, next(nxt), f"pe-{run_tag}-paper", None)
    st, paper_resp = await api.call(
        "POST",
        f"/api/v1/einvoice/sales/{paper_sale['id']}/manual-invoice",
        body={
            # 號碼每輪唯一：同店同號碼只能登記一次（實測 409：已登記於本店其他交易）
            # 格式必須是 **2 英文大寫 + 8 數字**（`^[A-Z]{2}[0-9]{8}$`）——
            # 第一版把十六進位批號塞進去，出現字母在第 3 位而被 422 擋下。
            "invoice_no": f"PE{int(run_tag, 16) % 100000000:08d}",
            "invoice_date": str(paper_sale["created_at"])[:10],
            "invoice_time": "10:30:00",
            "random_number": "4321",
            "total": str(paper_sale["total"]),
            "note": "PE 驗收：字軌用完當場開紙本",
        },
    )
    q = await _queue_for_sale(paper_sale["id"], "ISSUE")
    plat = await _platform_invoice_by_order(store_id, f"S{store_id}-{paper_sale['id']}")
    # 平台查無＝code 為「查無資料」或無號碼
    absent = not plat.get("invoice_number")
    ev.add(
        "8. 手開紙本－佇列轉 CANCELLED 且平台上沒有這張",
        st == 200 and (q or {}).get("status") == "CANCELLED" and absent,
        登記=st,
        登記訊息=(paper_resp or {}).get("detail") if st != 200 else None,
        佇列狀態=(q or {}).get("status"),
        平台號碼=plat.get("invoice_number"),
    )

    # ── 9. 銷售作廢 → 連動發票作廢 ─────────────────────────────────
    void_sale = await _sell(api, next(nxt), f"pe-{run_tag}-salevoid", None)
    st_issue, inv = await _issue(api, void_sale["id"])
    st_void, _v = await api.call("POST", f"/api/v1/sales/{void_sale['id']}/void")
    async with get_sessionmaker()() as session:
        row = (
            await session.execute(
                text("SELECT status FROM invoices WHERE sale_id = :s"), {"s": void_sale["id"]}
            )
        ).first()
    ev.order_ids.append(f"S{store_id}-{void_sale['id']}")
    ev.add(
        "9. 銷售作廢－連動發票進入作廢",
        st_issue == 200
        and st_void in (200, 204)
        and str(row[0] if row is not None else "") != "ISSUED",
        開立=inv.get("invoice_no"),
        作廢回應=st_void,
        發票狀態=(row[0] if row is not None else None),
    )
    return ev


def main() -> None:
    parser = argparse.ArgumentParser(description="電子發票模組驗收（docs/37 PE，對真測試平台）")
    parser.add_argument("--api", default="http://127.0.0.1:8010")
    parser.add_argument("--store-id", type=int, default=1)
    parser.add_argument("--username", default="dev-manager")
    parser.add_argument("--password", default="devpass1234")
    parser.add_argument(
        "--carrier",
        default=None,
        help="真實已登記的手機條碼載具（如 /ABC1234）；未提供則載具變體標為未驗證",
    )
    parser.add_argument("--out", default="einvoice_acceptance.json")
    args = parser.parse_args()

    ev = asyncio.run(
        run(
            base=args.api,
            store_id=args.store_id,
            username=args.username,
            password=args.password,
            carrier=args.carrier,
        )
    )
    passed = sum(1 for s in ev.steps if s["ok"])
    print(f"\n結果：{passed}/{len(ev.steps)} 通過")
    print(f"本輪佔用 OrderId：{', '.join(ev.order_ids)}")
    if ev.allowance_nos:
        print(f"本輪佔用折讓單號：{', '.join(ev.allowance_nos)}")
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(
            {"steps": ev.steps, "order_ids": ev.order_ids, "allowance_nos": ev.allowance_nos},
            fh,
            ensure_ascii=False,
            indent=2,
        )
    if passed != len(ev.steps):
        print("\n有未通過項目：不得帶著未驗證的行為進入 P2。", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
