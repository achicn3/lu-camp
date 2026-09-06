// @vitest-environment jsdom
// LINE Pay 載具自動帶入後的完成畫面提示（2026-09-06 裁示）。
//
// 為什麼一定要提示：有載具就**不印紙本**，客人沒看到紙會以為沒開發票；
// 而客人若說「我沒有載具」，店員也要能當場發現對不上。
import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api-types";
// **與畫面共用同一份述詞**：測試自己抄一份的話，把 page.tsx 的提示整塊刪掉測試照樣綠。
import { showsLinePayCarrierNote as showsCarrierNote } from "@/lib/invoice-carrier-note";

type InvoiceRead = components["schemas"]["InvoiceRead"];

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
