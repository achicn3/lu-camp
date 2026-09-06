// @vitest-environment jsdom
// LINE Pay 載具自動帶入後的完成畫面提示（2026-09-06 裁示）。
//
// 為什麼一定要提示：有載具就**不印紙本**，客人沒看到紙會以為沒開發票；
// 而客人若說「我沒有載具」，店員也要能當場發現對不上。
import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api-types";

type InvoiceRead = components["schemas"]["InvoiceRead"];

/** 完成畫面顯示載具提示的條件：發票有載具，而店員的載具欄是空的（＝自動帶入的）。 */
function showsCarrierNote(invoice: InvoiceRead | null, clerkCarrierInput: string): boolean {
  return invoice != null && invoice.carrier_id != null && clerkCarrierInput === "";
}

const withCarrier = { carrier_id: "/ABC1234" } as InvoiceRead;
const withoutCarrier = { carrier_id: null } as InvoiceRead;

describe("載具自動帶入的提示條件", () => {
  it("店員沒打載具、發票卻有 → 提示（那是 LINE Pay 帶進來的）", () => {
    expect(showsCarrierNote(withCarrier, "")).toBe(true);
  });

  it("店員自己打的載具 → 不提示（他知道自己打了什麼）", () => {
    expect(showsCarrierNote(withCarrier, "/ABC1234")).toBe(false);
  });

  it("發票沒有載具 → 不提示（會照常印出發票）", () => {
    expect(showsCarrierNote(withoutCarrier, "")).toBe(false);
  });

  it("沒有發票（未啟用電子發票或零元單）→ 不提示", () => {
    expect(showsCarrierNote(null, "")).toBe(false);
  });
});
