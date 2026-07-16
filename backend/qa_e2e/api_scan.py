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
    }
    out: dict[str, Any] = {}
    for k, sql in q.items():
        out[k] = await conn.fetchval(sql)
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

        for raw_path, methods in sorted(spec["paths"].items()):
            get = methods.get("get")
            if get is None:
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
        naive = {
            "date_from": "2026-01-01T00:00:00",
            "date_to": "2026-03-01T00:00:00",
            "granularity": "day",
        }
        reversed_range = {
            "date_from": "2026-03-01T00:00:00Z",
            "date_to": "2026-01-01T00:00:00Z",
            "granularity": "day",
        }
        edge_cases: list[tuple[str, dict[str, Any], str]] = [
            ("/api/v1/reports/trends", naive, "naive-datetime(F-1 回歸)"),
            ("/api/v1/reports/trends", reversed_range, "反向區間"),
            ("/api/v1/contacts", {"limit": 100000}, "超大分頁"),
            ("/api/v1/sales", {"limit": 0}, "零分頁"),
        ]
        for path, params2, label in edge_cases:
            t0 = time.perf_counter()
            try:
                resp = await client.get(path, params=params2, headers=headers)
                results.append(
                    {
                        "path": f"{path} [{label}]",
                        "url": path,
                        "status": resp.status_code,
                        "ms": round((time.perf_counter() - t0) * 1000, 1),
                    }
                )
            except Exception as exc:
                results.append({"path": f"{path} [{label}]", "status": -1, "err": str(exc)[:150]})

    server_errors = [r for r in results if r["status"] >= 500 or r["status"] == -1]
    slow = sorted(
        (r for r in results if r.get("ms", 0) > SLOW_MS), key=lambda r: -r["ms"]
    )
    report = {
        "base": BASE,
        "total": len(results),
        "server_errors": server_errors,
        "slow_over_1s": slow,
        "results": sorted(results, key=lambda r: -r.get("ms", 0)),
    }
    Path(__file__).with_name("api_scan_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(f"掃描 {len(results)} 端點；5xx/連線失敗 {len(server_errors)}；>1s {len(slow)}")
    for r in server_errors:
        print(f"  [5xx] {r['path']} → {r['status']} {r.get('err', '')}")
    for r in slow[:10]:
        print(f"  [slow] {r['path']} {r['ms']}ms")


if __name__ == "__main__":
    asyncio.run(main())
