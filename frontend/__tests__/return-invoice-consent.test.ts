import { describe, expect, it } from "vitest";

import {
  invoiceActionLabel,
  returnSubmitBlockers,
  type ReturnInvoicePreview,
} from "@/features/returns/invoice-consent";

function preview(overrides: Partial<ReturnInvoicePreview> = {}): ReturnInvoicePreview {
  return {
    is_full_return: true,
    invoice_action: "VOID",
    requires_paper_recall: true,
    requires_customer_consent: true,
    reason: "整筆退貨且原發票為本月開立：作廢原發票。需先向客人收回紙本證明聯。",
    refund_total: "500",
    unreturned_gifts: [],
    ...overrides,
  };
}

describe("invoiceActionLabel", () => {
  it("以店員看得懂的字說明會做什麼", () => {
    expect(invoiceActionLabel("VOID")).toBe("作廢原發票");
    expect(invoiceActionLabel("ALLOWANCE")).toBe("開立折讓單");
    expect(invoiceActionLabel("NONE")).toBe("不處理發票");
    expect(invoiceActionLabel("REVIEW_REQUIRED")).toBe("需人工處理");
  });
});

describe("returnSubmitBlockers", () => {
  it("預覽尚未回來時不擋——避免畫面未載入就鎖死按鈕", () => {
    expect(returnSubmitBlockers(null, { paperRecalled: false, consentTaskSigned: false })).toEqual(
      [],
    );
  });

  it("要作廢且有紙本：未確認收回即不得退貨", () => {
    const blockers = returnSubmitBlockers(preview(), {
      paperRecalled: false,
      consentTaskSigned: true,
    });
    expect(blockers).toContain("請先向客人收回發票證明聯（紙本）並勾選確認");
  });

  it("要作廢或折讓：未取得客人簽名同意即不得退貨", () => {
    const blockers = returnSubmitBlockers(preview({ requires_paper_recall: false }), {
      paperRecalled: false,
      consentTaskSigned: false,
    });
    expect(blockers).toContain("請先請客人於顧客螢幕簽名同意");
  });

  it("兩項都完成即可送出", () => {
    expect(
      returnSubmitBlockers(preview(), { paperRecalled: true, consentTaskSigned: true }),
    ).toEqual([]);
  });

  it("載具/捐贈發票無紙本可收回，不得無故要求勾選", () => {
    expect(
      returnSubmitBlockers(preview({ requires_paper_recall: false }), {
        paperRecalled: false,
        consentTaskSigned: true,
      }),
    ).toEqual([]);
  });

  it("沒有發票的交易不需要任何額外確認", () => {
    const none = preview({
      invoice_action: "NONE",
      requires_paper_recall: false,
      requires_customer_consent: false,
      reason: "原交易沒有已開立的發票，本次退貨不涉及發票處置。",
    });
    expect(
      returnSubmitBlockers(none, { paperRecalled: false, consentTaskSigned: false }),
    ).toEqual([]);
  });

  it("狀態未收斂時一律擋下，不讓店員硬送", () => {
    const review = preview({ invoice_action: "REVIEW_REQUIRED", requires_paper_recall: false });
    expect(
      returnSubmitBlockers(review, { paperRecalled: true, consentTaskSigned: true }),
    ).toContain("原發票狀態尚未確認，請待處理完成或聯繫管理者");
  });
});
