// 收購明細送代理：排隊收購的寄售品一起印（docs/42 §13；店主 2026-10-02）。
import { afterEach, describe, expect, it, vi } from "vitest";

import { printAcquisitionReceipt } from "@/lib/agent";

afterEach(() => vi.unstubAllGlobals());

function capture(): { body: () => Record<string, unknown> } {
  let sent: Record<string, unknown> = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: unknown, init?: RequestInit) => {
      sent = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
      return new Response('{"status":"ok"}', { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
  return { body: () => sent };
}

const base = {
  storeId: 1,
  acquisitionId: 9,
  sellerName: "王小明",
  createdAt: "2026-10-02T08:00:00Z",
  signaturePngBase64: "png",
};

describe("printAcquisitionReceipt", () => {
  it("帶寄售品（名稱、寄售售價、抽成）", async () => {
    const sent = capture();
    await printAcquisitionReceipt({
      ...base,
      items: [{ name: "營燈", amount: "300" }],
      total: "300",
      payoutMethod: "CASH",
      consignments: [{ name: "帳篷", listed_price: "6000", commission_pct: 50 }],
    });
    expect(sent.body()).toMatchObject({
      payout_method: "CASH",
      consignments: [{ name: "帳篷", listed_price: "6000", commission_pct: 50 }],
    });
  });

  it("只賣寄售：沒有撥款方式（null）", async () => {
    const sent = capture();
    await printAcquisitionReceipt({
      ...base,
      items: [],
      total: "0",
      payoutMethod: null,
      consignments: [{ name: "帳篷", listed_price: "6000", commission_pct: 50 }],
    });
    expect(sent.body().payout_method).toBeNull();
  });

  it("舊呼叫端不帶寄售：送空陣列", async () => {
    const sent = capture();
    await printAcquisitionReceipt({ ...base, items: [{ name: "a", amount: "1" }], total: "1", payoutMethod: "CASH" });
    expect(sent.body().consignments).toEqual([]);
  });
});
