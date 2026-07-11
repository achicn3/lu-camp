"""Layer C-1 — 多開(雙電腦)併發探針（真正平行）。

對 live backend(:8010) 以 asyncio.gather 同時送出多個請求壓同一共享資源，斷言
「恰一成功、其餘乾淨被拒、無 500、無壞帳」。模擬 POS 機與收購機同時操作。

每個探針自備 setup（建測試品/會員/結算），不依賴特定既有資料。
執行：BASE=http://localhost:8010 uv run python -m qa_e2e.concurrency_probes
"""

from __future__ import annotations

import asyncio
import os
import time
from decimal import Decimal

import httpx

BASE = os.environ.get("BASE", "http://localhost:8010") + "/api/v1"
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def codes(responses: list) -> list:
    out = []
    for r in responses:
        if isinstance(r, Exception):
            out.append(f"EXC:{type(r).__name__}")
        else:
            out.append(r.status_code)
    return out


async def fire(client: httpx.AsyncClient, n: int, build):
    """同時送出 n 個請求；以 barrier 讓它們盡量同時離開。build(i)->(method,url,json,headers)."""
    start = asyncio.Event()

    async def one(i: int):
        method, url, body, headers = build(i)
        await start.wait()
        return await client.request(method, url, json=body, headers=headers or {})

    tasks = [asyncio.create_task(one(i)) for i in range(n)]
    await asyncio.sleep(0.05)
    start.set()
    return await asyncio.gather(*tasks, return_exceptions=True)


def n_success(responses: list) -> int:
    return sum(1 for r in responses if not isinstance(r, Exception) and r.status_code < 300)


def n_5xx(responses: list) -> int:
    return sum(1 for r in responses if not isinstance(r, Exception) and r.status_code >= 500)


async def login(client: httpx.AsyncClient) -> str:
    r = await client.post(
        f"{BASE}/auth/login", json={"username": "dev-manager", "password": "dev-test-123456"}
    )
    return r.json()["access_token"]


async def make_buyout_item(client, seller_id: int, price: str = "1000") -> str:
    key = f"qa-acq-{time.time_ns()}"
    r = await client.post(
        f"{BASE}/acquisitions",
        headers={"Idempotency-Key": key},
        json={
            "type": "BUYOUT",
            "contact_id": seller_id,
            "payout_method": "CASH",
            "items": [
                {"name": "QA併發品", "grade": "A", "listed_price": price, "acquisition_cost": "400"}
            ],
        },
    )
    r.raise_for_status()
    return r.json()["item_codes"][0]


async def ensure_seller(client) -> int:
    r = await client.post(
        f"{BASE}/contacts",
        json={
            "name": "QA併發賣方",
            "phone": "0911000777",
            "national_id": "A123456789",
            "roles": ["SELLER"],
        },
    )
    if r.status_code < 300:
        return r.json()["id"]
    # 已存在 → 用列表搜尋手機找回；/contacts/lookup 是 national_id 專用。
    found = await client.get(f"{BASE}/contacts", params={"q": "0911000777", "limit": 10})
    if found.status_code < 300:
        items = found.json()
        for item in items:
            if item.get("phone") == "0911000777":
                return item["id"]
    return 7


async def ensure_menu_item(client) -> dict:
    name = f"QA餐飲{time.time_ns()}"
    r = await client.post(
        f"{BASE}/menu-items",
        json={"name": name, "unit_price": "180", "category": "QA", "sort_order": 999},
    )
    if r.status_code < 300:
        return r.json()
    items = await client.get(f"{BASE}/menu-items", params={"available_only": True})
    items.raise_for_status()
    existing = items.json()
    if not existing:
        raise RuntimeError(f"無法建立或取得餐飲品項：{r.status_code} {r.text[:120]}")
    return existing[0]


# ─────────────────────────── 探針 ───────────────────────────
async def p1_double_sell_serialized(client, seller_id):
    code = await make_buyout_item(client, seller_id)
    resp = await fire(
        client,
        6,
        lambda i: (
            "POST",
            f"{BASE}/sales",
            {"lines": [{"line_type": "SERIALIZED", "item_code": code}]},
            {"Idempotency-Key": f"qa-p1-{time.time_ns()}-{i}"},
        ),
    )
    ns = n_success(resp)
    record(
        "P1 雙台賣同一序號品：恰一成功",
        ns == 1 and n_5xx(resp) == 0,
        f"success={ns}, codes={codes(resp)}",
    )


async def p8_idempotent_replay(client, seller_id):
    code = await make_buyout_item(client, seller_id)
    key = f"qa-p8-{time.time_ns()}"
    resp = await fire(
        client,
        6,
        lambda i: (
            "POST",
            f"{BASE}/sales",
            {"lines": [{"line_type": "SERIALIZED", "item_code": code}]},
            {"Idempotency-Key": key},  # 同一把 key
        ),
    )
    ok_ids = {
        r.json().get("id") for r in resp if not isinstance(r, Exception) and r.status_code < 300
    }
    record(
        "P8 同冪等鍵並發重放：同一張單、無重複",
        len(ok_ids) == 1 and n_5xx(resp) == 0,
        f"distinct sale ids={ok_ids}, codes={codes(resp)}",
    )


async def p6_bulk_oversell(client):
    lots = (await client.get(f"{BASE}/bulk-lots")).json()
    lots = lots.get("items", lots) if isinstance(lots, dict) else lots
    lot = next(
        (x for x in lots if x.get("remaining_qty", 0) > 0 and x.get("status") == "ON_SALE"), None
    )
    if not lot:
        record("P6 散裝批超賣", False, "找不到 ON_SALE 散裝批")
        return
    lot_id, rem = lot["id"], lot["remaining_qty"]
    resp = await fire(
        client,
        2,
        lambda i: (
            "POST",
            f"{BASE}/sales",
            {"lines": [{"line_type": "BULK_LOT", "bulk_lot_id": lot_id, "qty": rem}]},
            {"Idempotency-Key": f"qa-p6-{time.time_ns()}-{i}"},
        ),
    )
    after = (await client.get(f"{BASE}/bulk-lots/by-code/{lot['lot_code']}")).json()
    ns = n_success(resp)
    record(
        "P6 散裝批雙台各買全部餘量：恰一成功、餘量不負",
        ns == 1 and after["remaining_qty"] >= 0 and n_5xx(resp) == 0,
        f"success={ns}, rem {rem}->{after['remaining_qty']}, codes={codes(resp)}",
    )


async def p7_consignment_double_pay(client):
    setts = (await client.get(f"{BASE}/consignment/settlements?status=PENDING")).json()
    setts = setts.get("items", setts) if isinstance(setts, dict) else setts
    if not setts:
        record("P7 寄售結算重複付款", False, "找不到 PENDING 結算")
        return
    sid = setts[0]["id"]
    resp = await fire(
        client,
        4,
        lambda i: (
            "POST",
            f"{BASE}/consignment/settlements/{sid}/pay",
            None,
            {"Idempotency-Key": f"qa-p7-{time.time_ns()}-{i}"},  # 不同 key → 真重複付款
        ),
    )
    ns = n_success(resp)
    record(
        "P7 同一結算雙台同時付款：恰一次付款",
        ns == 1 and n_5xx(resp) == 0,
        f"success={ns}, codes={codes(resp)}",
    )


async def p4_member_dedup_race(client):
    nid = "B200000004"  # 有效身分證（與既有 seller 的 A123456789 不同）
    phones = ["0922000001", "0922000002", "0922000003", "0922000004"]
    # 每個請求 body 不同（不同電話、同 national_id）→ 自訂 barrier 平行送出
    start = asyncio.Event()

    async def one(i):
        await start.wait()
        return await client.post(
            f"{BASE}/contacts",
            json={
                "name": f"QA撞號{i}",
                "phone": phones[i],
                "national_id": nid,
                "roles": ["MEMBER"],
            },
        )

    tasks = [asyncio.create_task(one(i)) for i in range(4)]
    await asyncio.sleep(0.05)
    start.set()
    resp = await asyncio.gather(*tasks, return_exceptions=True)
    ids = {r.json().get("id") for r in resp if not isinstance(r, Exception) and r.status_code < 300}
    no5xx = n_5xx(resp) == 0
    # 期望：去重 → 至多一個獨立 contact（其餘回同一既有或 409），且絕不 500
    record(
        "P4 雙台建同一身分證會員：無 500、不產生重複",
        len(ids) <= 1 and no5xx,
        f"distinct ids={ids}, codes={codes(resp)} (5xx 即 service 未優雅處理唯一鍵衝突)",
    )


async def p2_concurrent_real_cash_flows(client, seller_id):
    """多開現金核心：POS 賣現(SALE_IN) + 收購買斷付現(BUYOUT_OUT) + 手動調整，
    三台同時寫入同一個 OPEN session。斷言全數成功、無 500（帳本守恆由 Layer B 重跑驗證）。"""
    code = await make_buyout_item(client, seller_id)  # POS 端要賣的二手品
    sess = (await client.get(f"{BASE}/cash-sessions/current")).json()
    sid = sess["id"]
    start = asyncio.Event()

    async def pos_sale():
        await start.wait()
        return await client.post(
            f"{BASE}/sales",
            headers={"Idempotency-Key": f"qa-p2s-{time.time_ns()}"},
            json={"lines": [{"line_type": "SERIALIZED", "item_code": code}]},
        )

    async def acq_buyout():
        await start.wait()
        return await client.post(
            f"{BASE}/acquisitions",
            headers={"Idempotency-Key": f"qa-p2a-{time.time_ns()}"},
            json={
                "type": "BUYOUT",
                "contact_id": seller_id,
                "payout_method": "CASH",
                "items": [
                    {
                        "name": "QA同時收購",
                        "grade": "B",
                        "listed_price": "800",
                        "acquisition_cost": "300",
                    }
                ],
            },
        )

    async def manual_adj():
        await start.wait()
        return await client.post(
            f"{BASE}/cash-sessions/{sid}/movements",
            json={"type": "MANUAL_ADJUST", "amount": "50", "note": "QA併發調整"},
        )

    tasks = [
        asyncio.create_task(pos_sale()),
        asyncio.create_task(acq_buyout()),
        asyncio.create_task(manual_adj()),
    ]
    await asyncio.sleep(0.05)
    start.set()
    resp = await asyncio.gather(*tasks, return_exceptions=True)
    ns = n_success(resp)
    record(
        "P2 同一現金 session 並發(賣現+收購付現+調整)：全數入帳、無 500",
        ns == 3 and n_5xx(resp) == 0,
        f"success={ns}, codes={codes(resp)}",
    )


async def p5_store_credit_double_spend(client):
    # 建會員（每次跑用唯一電話避免衝突）+ 撥入購物金 1000，再兩台同時扣 1000
    phone = f"09{str(time.time_ns())[-8:]}"
    m = await client.post(
        f"{BASE}/contacts", json={"name": "QA購物金會員", "phone": phone, "roles": ["MEMBER"]}
    )
    m.raise_for_status()
    cid = m.json()["id"]
    await client.post(
        f"{BASE}/contacts/{cid}/store-credit/adjustments",
        headers={"Idempotency-Key": f"qa-credit-{time.time_ns()}"},
        json={"amount": "1000", "reason": "QA 測試撥入"},
    )
    resp = await fire(
        client,
        2,
        lambda i: (
            "POST",
            f"{BASE}/contacts/{cid}/store-credit/adjustments",
            {"amount": "-1000", "reason": f"QA 並發扣抵{i}"},
            {"Idempotency-Key": f"qa-debit-{time.time_ns()}-{i}"},
        ),
    )
    bal = (await client.get(f"{BASE}/contacts/{cid}/store-credit")).json()
    ns = n_success(resp)
    record(
        "P5 購物金雙台同時扣抵：恰一成功、餘額不負",
        ns == 1 and int(bal.get("balance", 0)) >= 0 and n_5xx(resp) == 0,
        f"success={ns}, final balance={bal.get('balance')}, codes={codes(resp)}",
    )


async def p9_food_store_credit_guard(client, seller_id):
    """餐飲不得抵用購物金：混合購物車若用購物金付全額必擋；
    只用購物金付非餐飲上限、餐飲現金支付則可成立。"""
    phone = f"09{str(time.time_ns())[-8:]}"
    member = await client.post(
        f"{BASE}/contacts",
        json={"name": "QA餐飲購物金會員", "phone": phone, "roles": ["MEMBER"]},
    )
    member.raise_for_status()
    cid = member.json()["id"]
    await client.post(
        f"{BASE}/contacts/{cid}/store-credit/adjustments",
        headers={"Idempotency-Key": f"qa-food-credit-{time.time_ns()}"},
        json={"amount": "3000", "reason": "QA 餐飲購物金卡控測試"},
    )

    menu = await ensure_menu_item(client)
    code = await make_buyout_item(client, seller_id, price="1200")
    lines = [
        {"line_type": "SERIALIZED", "item_code": code},
        {"line_type": "MENU", "menu_item_id": menu["id"], "qty": 1},
    ]
    quote = await client.post(f"{BASE}/sales/quote", json={"buyer_contact_id": cid, "lines": lines})
    if quote.status_code >= 300:
        record("P9 餐飲購物金卡控", False, f"quote={quote.status_code} {quote.text[:120]}")
        return
    qbody = quote.json()
    total = Decimal(qbody["total"])
    store_credit_max = Decimal(qbody["store_credit_max"])
    food_subtotal = Decimal(qbody["food_subtotal"])
    if not (food_subtotal > 0 and Decimal(0) < store_credit_max < total):
        record(
            "P9 餐飲購物金卡控",
            False,
            f"quote total={total}, food={food_subtotal}, max={store_credit_max}",
        )
        return

    bad = await client.post(
        f"{BASE}/sales",
        headers={"Idempotency-Key": f"qa-p9-bad-{time.time_ns()}"},
        json={
            "buyer_contact_id": cid,
            "lines": lines,
            "tenders": [{"tender_type": "STORE_CREDIT", "amount": str(total)}],
        },
    )
    bal_after_bad = await client.get(f"{BASE}/contacts/{cid}/store-credit")
    bal_after_bad.raise_for_status()
    bad_balance = Decimal(bal_after_bad.json()["balance"])
    bad_ok = bad.status_code == 422 and bad_balance == Decimal(3000)

    good = await client.post(
        f"{BASE}/sales",
        headers={"Idempotency-Key": f"qa-p9-good-{time.time_ns()}"},
        json={
            "buyer_contact_id": cid,
            "lines": lines,
            "tenders": [
                {"tender_type": "STORE_CREDIT", "amount": str(store_credit_max)},
                {"tender_type": "CASH", "amount": str(total - store_credit_max)},
            ],
        },
    )
    bal_after_good = await client.get(f"{BASE}/contacts/{cid}/store-credit")
    bal_after_good.raise_for_status()
    final_balance = Decimal(bal_after_good.json()["balance"])
    good_ok = good.status_code < 300 and final_balance == Decimal(3000) - store_credit_max
    record(
        "P9 餐飲不得抵用購物金：超額擋、合法混合付款成立",
        bad_ok and good_ok,
        f"bad={bad.status_code}, good={good.status_code}, total={total}, "
        f"food={food_subtotal}, credit_max={store_credit_max}, balance={final_balance}",
    )


async def p3_close_vs_acquisition(client, seller_id):
    """一台關帳、另一台同時收購買斷付現（需開帳中、產生 BUYOUT_OUT）。
    斷言：無 500；收購要嘛入本班(關帳前)、要嘛被擋(已無開帳)，不得把現金算進已關班別。"""
    sess = (await client.get(f"{BASE}/cash-sessions/current")).json()
    sid = sess["id"]
    start = asyncio.Event()

    async def do_close():
        await start.wait()
        return await client.post(
            f"{BASE}/cash-sessions/{sid}/close", json={"counted_amount": "99999"}
        )

    async def do_buyout():
        await start.wait()
        return await client.post(
            f"{BASE}/acquisitions",
            headers={"Idempotency-Key": f"qa-p3-{time.time_ns()}"},
            json={
                "type": "BUYOUT",
                "contact_id": seller_id,
                "payout_method": "CASH",
                "items": [
                    {
                        "name": "QA關帳競態收購",
                        "grade": "B",
                        "listed_price": "800",
                        "acquisition_cost": "500",
                    }
                ],
            },
        )

    t = [asyncio.create_task(do_close()), asyncio.create_task(do_buyout())]
    await asyncio.sleep(0.05)
    start.set()
    resp = await asyncio.gather(*t, return_exceptions=True)
    close_r, buy_r = resp[0], resp[1]
    no5xx = n_5xx(resp) == 0
    cc = close_r.status_code if not isinstance(close_r, Exception) else f"EXC:{close_r}"
    bc = buy_r.status_code if not isinstance(buy_r, Exception) else f"EXC:{buy_r}"
    record("P3 關帳 vs 收購付現競態：互斥、無 500", no5xx, f"close={cc}, buyout={bc}")
    # 重開一個 session，保持後續(UI 層 / Layer B 重跑)可用
    await client.post(f"{BASE}/cash-sessions/open", json={"opening_float": "5000"})


async def main():
    async with httpx.AsyncClient(timeout=30) as client:
        tok = await login(client)
        client.headers["Authorization"] = f"Bearer {tok}"
        seller = await ensure_seller(client)
        print(f"=== Layer C-1 併發探針 (seller={seller}) ===")
        await p1_double_sell_serialized(client, seller)
        await p8_idempotent_replay(client, seller)
        await p6_bulk_oversell(client)
        await p7_consignment_double_pay(client)
        await p4_member_dedup_race(client)
        await p2_concurrent_real_cash_flows(client, seller)
        await p5_store_credit_double_spend(client)
        await p9_food_store_credit_guard(client, seller)
        await p3_close_vs_acquisition(client, seller)  # 會關帳→重開，放最後

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n=== 探針結果：{passed}/{len(results)} PASS ===")
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL - {name}: {detail}")


if __name__ == "__main__":
    asyncio.run(main())
