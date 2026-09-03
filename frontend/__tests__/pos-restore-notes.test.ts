// 還原購物車時要重新取回商品備註，且**取不到時不得當成「沒有備註」**
// （Codex 對抗式審查第一輪＋第二輪 high）。
//
// 沒有重新取回：重整／崩潰復原／換店員接手之後所有商品都被當成沒有備註 → 結帳不再提醒。
// 取不到卻當成沒有：短暫 401／500／斷線就會讓「先別賣」無聲消失——正是要避免的事。
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CartLine } from "@/features/pos/cart";
import { withFreshNotes } from "@/features/pos/restoreNotes";
import { setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(route: (url: string) => Response | null) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      const resp = route(url);
      if (resp) return resp;
      throw new Error(`unmatched fetch: ${url}`);
    }),
  );
}

const BASE: CartLine = {
  key: "x",
  lineType: "SERIALIZED",
  description: "外套",
  unitPrice: 1000,
  qty: 1,
};

afterEach(() => vi.unstubAllGlobals());

describe("withFreshNotes", () => {
  it("三種庫存型態都重新取回備註", async () => {
    setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
    stubFetch((url) => {
      if (url.includes("/serialized-items/by-code/S1-A"))
        return json({ item_code: "S1-A", note: "缺充電線" });
      if (url.includes("/catalog-products/12")) return json({ id: 12, note: "效期短" });
      if (url.includes("/bulk-lots/34")) return json({ id: 34, note: "請客人自己點" });
      return null;
    });

    const restored: CartLine[] = [
      { ...BASE, key: "S:S1-A", itemCode: "S1-A" },
      { ...BASE, key: "C:12", lineType: "CATALOG", catalogProductId: 12 },
      { ...BASE, key: "B:34", lineType: "BULK_LOT", bulkLotId: 34 },
    ];
    const out = await withFreshNotes(restored);
    expect(out.map((l) => l.note)).toEqual(["缺充電線", "效期短", "請客人自己點"]);
    expect(out.every((l) => l.noteUnknown !== true)).toBe(true);
  });

  it("備註被清空時還原成沒有備註（不留下過期提醒）", async () => {
    setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
    stubFetch((url) =>
      url.includes("/serialized-items/by-code/S1-A")
        ? json({ item_code: "S1-A", note: null })
        : null,
    );
    const restored: CartLine[] = [
      { ...BASE, key: "S:S1-A", itemCode: "S1-A", note: "掃碼時的舊備註" },
    ];
    const out = await withFreshNotes(restored);
    expect(out[0].note).toBeNull();
    expect(out[0].noteUnknown).toBeUndefined();
  });

  it.each([
    ["401（權杖剛過期）", 401],
    ["403", 403],
    ["500（後端暫時炸了）", 500],
  ])("%s：標記為未知，不得當成沒有備註", async (_label, status) => {
    setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
    stubFetch((url) =>
      url.includes("/serialized-items/by-code/S1-A") ? json({ detail: "x" }, status) : null,
    );
    const out = await withFreshNotes([{ ...BASE, key: "S:S1-A", itemCode: "S1-A" }]);
    expect(out[0].noteUnknown).toBe(true);
  });

  it("網路例外：同樣標記為未知（不是靜默略過）", async () => {
    setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }));
    const out = await withFreshNotes([{ ...BASE, key: "S:S1-A", itemCode: "S1-A" }]);
    expect(out[0].noteUnknown).toBe(true);
  });

  it("404（商品被刪/他店）視為確定沒有備註，不是讀取失敗", async () => {
    setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
    stubFetch((url) =>
      url.includes("/serialized-items/by-code/GONE") ? json({ detail: "找不到" }, 404) : null,
    );
    const out = await withFreshNotes([{ ...BASE, key: "S:GONE", itemCode: "GONE" }]);
    expect(out[0].noteUnknown).toBeUndefined();
    expect(out[0].note).toBeUndefined();
  });

  it("餐飲行沒有庫存備註，不發請求也不標記未知", async () => {
    setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
    const fetchSpy = vi.fn(async () => json({}));
    vi.stubGlobal("fetch", fetchSpy);
    const restored: CartLine[] = [
      { ...BASE, key: "MENU-5", lineType: "MENU", menuItemId: 5, description: "手沖" },
    ];
    expect(await withFreshNotes(restored)).toEqual(restored);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("後端不回應：逾時後標記未知並讓還原結束，不得無限等待", async () => {
    // 收銀台**永遠不能因為補備註而結不了帳**。伺服器收了連線卻不回應時，
    // 沒有逾時就會讓 restoring 一直是 true → 結帳鍵永久停用。
    setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => {})), // 永不 settle
    );
    const out = await withFreshNotes([{ ...BASE, key: "S:S1-A", itemCode: "S1-A" }], {
      timeoutMs: 10,
    });
    expect(out[0].noteUnknown).toBe(true);
  });

  it("逾時是逐行獨立的：一件卡住不拖累其他件", async () => {
    setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("HANG")) return await new Promise<Response>(() => {});
        return json({ item_code: "OK", note: "缺充電線" });
      }),
    );
    const out = await withFreshNotes(
      [
        { ...BASE, key: "S:HANG", itemCode: "HANG" },
        { ...BASE, key: "S:OK", itemCode: "OK" },
      ],
      { timeoutMs: 10 },
    );
    expect(out[0].noteUnknown).toBe(true);
    expect(out[1].note).toBe("缺充電線");
  });
});

