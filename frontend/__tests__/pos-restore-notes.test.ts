// 還原購物車時要重新取回商品備註（Codex 對抗式審查 high）。
// 沒有這一步，重整／崩潰復原／換店員接手之後所有商品都被當成沒有備註 →
// 結帳不再提醒，功能在最需要它的場合失效。
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
    expect((await withFreshNotes(restored)).map((l) => l.note)).toEqual([
      "缺充電線",
      "效期短",
      "請客人自己點",
    ]);
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
    expect((await withFreshNotes(restored))[0].note).toBeNull();
  });

  it("取不到（404／網路）就維持原樣，還原本身不中斷", async () => {
    setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
    stubFetch((url) =>
      url.includes("/serialized-items/by-code/GONE") ? json({ detail: "找不到" }, 404) : null,
    );
    const restored: CartLine[] = [{ ...BASE, key: "S:GONE", itemCode: "GONE" }];
    const out = await withFreshNotes(restored);
    expect(out).toHaveLength(1);
    expect(out[0].note).toBeUndefined();
  });

  it("餐飲行沒有庫存備註，不發請求也不改動", async () => {
    setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
    const fetchSpy = vi.fn(async () => json({}));
    vi.stubGlobal("fetch", fetchSpy);
    const restored: CartLine[] = [
      { ...BASE, key: "MENU-5", lineType: "MENU", menuItemId: 5, description: "手沖" },
    ];
    expect(await withFreshNotes(restored)).toEqual(restored);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
