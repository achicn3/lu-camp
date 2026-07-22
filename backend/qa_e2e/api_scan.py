"""Layer D — OpenAPI 驅動的全 GET 端點掃描（docs/27 Phase 2）。

對跑在 :8010、指向 lucamp_sim 的後端，把 openapi.json 的每個 GET 端點都打一輪：
- path 參數以 DB 撈到的真實 id 代入（打到非空資料路徑，而非空 404）
- 必要 query 參數以名稱啟發式補值（日期區間/granularity/q…）
- 記錄 status 與 latency；任何 5xx 即缺陷；>1s 列效能候選
- 邊界加測：trends 的 naive 日期（F-1 回歸）、超大分頁、空區間

執行（先起 server）：
    cd backend && SIM_API=http://127.0.0.1:8010 uv run python -m qa_e2e.api_scan
產出 qa_e2e/api_scan_report.json。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import httpx

BASE = os.environ.get("SIM_API", "http://127.0.0.1:8010")
PG = os.environ.get(
    "SIM_PG", "postgresql://lucamp:lucamp_dev_pw@127.0.0.1:1234/lucamp_sim"
)
USER = os.environ.get("SIM_USER", "dev-manager")
PASS = os.environ.get("SEED_USER_PASSWORD", "dev-test-123456")
SLOW_MS = 1000
REPORT_PATH = Path(
    os.environ.get("SIM_API_REPORT_PATH", str(Path(__file__).with_name("api_scan_report.json")))
)



async def _sample_ids() -> dict[str, Any]:
    """從 sim DB 撈各實體的真實 id，讓掃描打到有資料的路徑。"""
    conn = await asyncpg.connect(PG)
    q = {
        "contact_id": "SELECT id FROM contacts WHERE 'MEMBER'=ANY(roles) ORDER BY id LIMIT 1",
        "sale_id": "SELECT id FROM sales WHERE invoice_status <> 'VOID' ORDER BY id DESC LIMIT 1",
        "task_id": "SELECT id FROM signature_tasks WHERE status='SIGNED' ORDER BY id LIMIT 1",
        "purchase_order_id": "SELECT id FROM purchase_orders ORDER BY id DESC LIMIT 1",
        "supplier_id": "SELECT id FROM suppliers ORDER BY id LIMIT 1",
        "campaign_id": "SELECT id FROM campaigns ORDER BY id LIMIT 1",
        "stocktake_id": "SELECT id FROM stocktakes ORDER BY id LIMIT 1",
        "settlement_id": "SELECT id FROM consignment_settlements ORDER BY id LIMIT 1",
        "lot_id": "SELECT id FROM bulk_lots ORDER BY id LIMIT 1",
        "item_id": "SELECT id FROM serialized_items ORDER BY id LIMIT 1",
        "menu_item_id": "SELECT id FROM menu_items ORDER BY id LIMIT 1",
        "acquisition_id": "SELECT id FROM acquisitions ORDER BY id DESC LIMIT 1",
        "return_id": "SELECT id FROM returns ORDER BY id LIMIT 1",
        "receipt_id": "SELECT id FROM goods_receipts ORDER BY id LIMIT 1",
        "user_id": "SELECT id FROM users ORDER BY id LIMIT 1",
        "product_id": "SELECT id FROM catalog_products ORDER BY id LIMIT 1",
        "item_code": "SELECT item_code FROM serialized_items ORDER BY id LIMIT 1",
        "sku": "SELECT sku FROM catalog_products ORDER BY id LIMIT 1",
        "lot_code": "SELECT lot_code FROM bulk_lots ORDER BY id LIMIT 1",
    }
    out: dict[str, Any] = {}
    for k, sql in q.items():
        out[k] = await conn.fetchval(sql)
    # 發票樣本：sim 可能整輪關閉開票（無資料）→ None，由呼叫端明確標 skip 而非啞 404
    out["invoice_id"] = await conn.fetchval("SELECT id FROM invoices ORDER BY id LIMIT 1")
    # 一致 tuple：會員消費明細路由需要「該買方自己的銷售」（獨立取值會 404，Codex P2）
    row = await conn.fetchrow(
        "SELECT buyer_contact_id, id FROM sales "
        "WHERE buyer_contact_id IS NOT NULL AND invoice_status <> 'VOID' ORDER BY id DESC LIMIT 1"
    )
    if row is not None:
        out["contact_id"], out["sale_id"] = row[0], row[1]
    await conn.close()
    return out


def _fill_path(path: str, ids: dict[str, Any]) -> str | None:
    """把 /xx/{param} 的參數帶入真實值；對不上的參數用 1。"""

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        for key, val in ids.items():
            if key == name or key.replace("_id", "Id") == name or name.endswith(key):
                return str(val)
        if "code" in name.lower():
            return str(ids.get("item_code", "S1-000000"))
        return "1"

    if "{" not in path:
        return path
    return re.sub(r"\{([^}]+)\}", sub, path)


def _query_for(params: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in params:
        if p.get("in") != "query" or not p.get("required"):
            continue
        n = p["name"]
        if n in ("date_from", "from", "start", "starts_at"):
            out[n] = "2026-01-01T00:00:00Z"
        elif n in ("date_to", "to", "end", "ends_at"):
            out[n] = "2026-07-15T00:00:00Z"
        elif n == "granularity":
            out[n] = "month"
        elif n in ("date", "day", "business_date"):
            out[n] = "2026-07-10"
        elif n == "q":
            out[n] = "a"
        else:
            sch = p.get("schema", {})
            out[n] = sch.get("default", 1 if sch.get("type") == "integer" else "a")
    return out


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as boot:
        spec = (await boot.get("/openapi.json")).json()
    ids = await _sample_ids()
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        login_resp = await client.post(
            "/api/v1/auth/login", json={"username": USER, "password": PASS}
        )
        login_resp.raise_for_status()
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        skipped: list[dict[str, Any]] = []
        for raw_path, methods in sorted(spec["paths"].items()):
            get = methods.get("get")
            if get is None:
                continue
            if "/kiosk" in raw_path:
                # kiosk 端點需 KIOSK 角色 token（staff 被中央拒絕）→ 明確標記略過，
                # 不計入「已掃描」（Codex P2：403 不可謊稱覆蓋）。
                skipped.append(
                    {"path": raw_path, "reason": "KIOSK 角色專用（staff 403 by design）"}
                )
                continue
            if "{invoice_id}" in raw_path and ids.get("invoice_id") is None:
                skipped.append(
                    {"path": raw_path, "reason": "資料集無發票（einvoice 關閉情境）"}
                )
                continue
            path = _fill_path(raw_path, ids)
            if path is None:
                continue
            params = _query_for(get.get("parameters", []))
            t0 = time.perf_counter()
            try:
                resp = await client.get(path, params=params, headers=headers)
                ms = (time.perf_counter() - t0) * 1000
                results.append(
                    {
                        "path": raw_path,
                        "url": path,
                        "status": resp.status_code,
                        "ms": round(ms, 1),
                        "operation_id": get.get("operationId", ""),
                    }
                )
            except Exception as exc:
                results.append(
                    {"path": raw_path, "url": path, "status": -1, "ms": -1, "err": str(exc)[:150]}
                )

        # 邊界加測（F-1 回歸與極端參數；同樣只有 5xx 算缺陷）
        # 邊界加測：**必須用實際 query 別名 from/to**（先前誤用 date_from → 422 根本沒
        # 進到日期解析，F-1 回歸形同未測；Codex P1）。每案帶預期狀態並斷言。
        naive = {
            "from": "2026-01-01T00:00:00",
            "to": "2026-03-01T00:00:00",
            "granularity": "day",
        }
        reversed_range = {
            "from": "2026-03-01T00:00:00Z",
            "to": "2026-01-01T00:00:00Z",
            "granularity": "day",
        }
        edge_cases: list[tuple[str, dict[str, Any], str, set[int]]] = [
            # API 時間瞬間必須帶 offset；naive 值若被接受，部署主機時區會改變查詢語意。
            ("/api/v1/reports/trends", naive, "naive-datetime 應拒絕", {422}),
            ("/api/v1/reports/trends", reversed_range, "反向區間", {200, 400, 422}),
            # 分頁越界**必須被驗證擋下**（schema le=200/ge=1）——接受 200 等於允許驗證
            # 被拿掉而掃描仍綠（Codex 第二輪 P2）。
            ("/api/v1/contacts", {"limit": 100000}, "超大分頁", {422}),
            ("/api/v1/sales", {"limit": 0}, "零分頁", {422}),
        ]
        edge_failures = 0
        for path, params2, label, expected in edge_cases:
            t0 = time.perf_counter()
            try:
                resp = await client.get(path, params=params2, headers=headers)
                ok = resp.status_code in expected
                if not ok:
                    edge_failures += 1
                results.append(
                    {
                        "path": f"{path} [{label}]",
                        "url": path,
                        "status": resp.status_code,
                        "expected": sorted(expected),
                        "edge_ok": ok,
                        "ms": round((time.perf_counter() - t0) * 1000, 1),
                    }
                )
            except Exception as exc:
                edge_failures += 1
                results.append({"path": f"{path} [{label}]", "status": -1, "err": str(exc)[:150]})

    server_errors = [r for r in results if r["status"] >= 500 or r["status"] == -1]
    # 誠實分桶（Codex P2）：2xx 才算「已運動到」；4xx 是探針打不進去、列名單，不謊稱覆蓋。
    exercised = [r for r in results if 200 <= r["status"] < 300]
    not_exercised = [r for r in results if 300 <= r["status"] < 500]
    slow = sorted(
        (r for r in results if r.get("ms", 0) > SLOW_MS), key=lambda r: -r["ms"]
    )
    report = {
        "base": BASE,
        "total_probed": len(results),
        "exercised_2xx": len(exercised),
        "not_exercised_4xx": [
            {"path": r["path"], "status": r["status"]} for r in not_exercised
        ],
        "skipped": skipped,
        "edge_failures": edge_failures,
        "server_errors": server_errors,
        "slow_over_1s": slow,
        "results": sorted(results, key=lambda r: -r.get("ms", 0)),
    }
    await asyncio.to_thread(
        REPORT_PATH.write_text,
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"探測 {len(results)}：2xx 運動到 {len(exercised)}、4xx 未進入 {len(not_exercised)}、"
        f"略過 {len(skipped)}；5xx/連線失敗 {len(server_errors)}；邊界失敗 {edge_failures}；"
        f">1s {len(slow)}"
    )
    for r in not_exercised:
        print(f"  [4xx] {r['path']} → {r['status']}")
    for r in server_errors:
        print(f"  [5xx] {r['path']} → {r['status']} {r.get('err', '')}")
    for r in slow[:10]:
        print(f"  [slow] {r['path']} {r['ms']}ms")
    if server_errors or edge_failures:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
