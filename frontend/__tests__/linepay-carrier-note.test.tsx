// @vitest-environment jsdom
// LINE Pay 載具自動帶入後的完成畫面提示（2026-09-06 裁示）。
//
// 為什麼一定要提示：有載具就**不印紙本**，客人沒看到紙會以為沒開發票；
// 而客人若說「我沒有載具」，店員也要能當場發現對不上。
import { describe, expect, it } from "vitest";

import type { components } from "@/lib/api-types";
// **與畫面共用同一份述詞**：測試自己抄一份的話，把 page.tsx 的提示整塊刪掉測試照樣綠。
import {
  showsLinePayCarrierNote as showsCarrierNote,
  showsLinePayNoCarrierNote as showsNoCarrierNote,
} from "@/lib/invoice-carrier-note";

type InvoiceRead = components["schemas"]["InvoiceRead"];
type SaleRead = components["schemas"]["SaleRead"];

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

// 店主 2026-10-01：LINE Pay 沒回載具時照常印紙本，但要讓店員知道為什麼印了。
describe("LINE Pay 沒回載具的提示條件", () => {
  const plain = { carrier_id: null, buyer_tax_id: null, donate_mark: false } as InvoiceRead;
  const paidBy = (...types: string[]) =>
    ({ tenders: types.map((tender_type) => ({ tender_type })) }) as SaleRead;

  it("用 LINE Pay 付、發票沒有載具也沒統編／捐贈 → 提示已印紙本", () => {
    expect(showsNoCarrierNote(plain, paidBy("LINE_PAY"))).toBe(true);
  });

  it("購物金＋LINE Pay 混合付款也算", () => {
    expect(showsNoCarrierNote(plain, paidBy("STORE_CREDIT", "LINE_PAY"))).toBe(true);
  });

  it("不是 LINE Pay（現金、台灣Pay）→ 不提示", () => {
    expect(showsNoCarrierNote(plain, paidBy("CASH"))).toBe(false);
    expect(showsNoCarrierNote(plain, paidBy("TAIWAN_PAY"))).toBe(false);
  });

  it("有載具（LINE Pay 帶到或店員輸入）→ 不提示", () => {
    const carried = { ...plain, carrier_id: "/ABC1234" };
    expect(showsNoCarrierNote(carried, paidBy("LINE_PAY"))).toBe(false);
  });

  it("店員打了統編或選捐贈 → 本來就不會用載具，不提示", () => {
    const b2b = { ...plain, buyer_tax_id: "12345678" };
    expect(showsNoCarrierNote(b2b, paidBy("LINE_PAY"))).toBe(false);
    expect(showsNoCarrierNote({ ...plain, donate_mark: true }, paidBy("LINE_PAY"))).toBe(false);
  });

  it("沒有發票或沒有銷售 → 不提示", () => {
    expect(showsNoCarrierNote(null, paidBy("LINE_PAY"))).toBe(false);
    expect(showsNoCarrierNote(plain, null)).toBe(false);
  });
});
