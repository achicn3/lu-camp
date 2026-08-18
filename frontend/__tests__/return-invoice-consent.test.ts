import { describe, expect, it } from "vitest";

import {
  invoiceActionLabel,
  returnSubmitBlockers,
  type ReturnInvoicePreview,
  mayShowExternalRefundInstructions,
} from "@/features/returns/invoice-consent";

function preview(overrides: Partial<ReturnInvoicePreview> = {}): ReturnInvoicePreview {
  return {
    is_full_return: true,
    invoice_action: "VOID",
    manual_paper_resolvable: false,
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

describe("mayShowExternalRefundInstructions（docs/36）", () => {
  const clear = { paperRecalled: true, consentTaskSigned: true, manualPaperDisposed: true };

  it("preview 還沒回來時不得顯示外部退款指示", () => {
    expect(mayShowExternalRefundInstructions(null, clear)).toBe(false);
  });

  it("轉人工且無法解除時不得顯示——店員會照做把錢退出去，之後才被後端拒絕", () => {
    expect(
      mayShowExternalRefundInstructions(
        preview({
          invoice_action: "REVIEW_REQUIRED",
          manual_paper_resolvable: false,
          requires_paper_recall: false,
        }),
        clear,
      ),
    ).toBe(false);
  });

  it("手開紙本經店長確認後**必須**顯示，否則台灣Pay 的手開紙本退貨永遠退不了", () => {
    const manualPaper = preview({
      invoice_action: "REVIEW_REQUIRED",
      manual_paper_resolvable: true,
      requires_paper_recall: false,
      requires_customer_consent: false,
    });
    expect(
      mayShowExternalRefundInstructions(manualPaper, {
        paperRecalled: false,
        consentTaskSigned: false,
        manualPaperDisposed: false,
      }),
    ).toBe(false);
    expect(mayShowExternalRefundInstructions(manualPaper, clear)).toBe(true);
  });

  // 現行設計：台灣Pay 退款確認與收回紙本、簽名同意**三重確認同時出現**
  // （scripts/return-invoice-smoke.mjs 情境 7）。是否改成序列化（退款排最後一關）
  // 屬設計變更，待裁示——這裡把現行行為釘住，避免被無意改掉。
  it("紙本尚未收回／尚未簽名時仍會顯示：三重確認同時出現（現行設計）", () => {
    expect(
      mayShowExternalRefundInstructions(preview(), {
        paperRecalled: false,
        consentTaskSigned: false,
      }),
    ).toBe(true);
  });

  it("前置全部完成的折讓/作廢照舊顯示", () => {
    for (const action of ["ALLOWANCE", "VOID", "NONE"]) {
      expect(mayShowExternalRefundInstructions(preview({ invoice_action: action }), clear)).toBe(
        true,
      );
    }
  });
});

describe("手開紙本的轉人工可由店長解除（docs/36）", () => {
  const manualPaper = preview({
    invoice_action: "REVIEW_REQUIRED",
    manual_paper_resolvable: true,
    requires_paper_recall: false,
    requires_customer_consent: false,
    reason: "手開紙本",
  });

  it("未確認前擋下，且訊息要說得出該做什麼", () => {
    const blockers = returnSubmitBlockers(manualPaper, {
      paperRecalled: false,
      consentTaskSigned: false,
    });
    expect(blockers).toHaveLength(1);
    expect(blockers[0]).toContain("紙本");
  });

  it("店長確認後解除——否則送出鍵永遠停用，退貨路徑等於不存在", () => {
    expect(
      returnSubmitBlockers(manualPaper, {
        paperRecalled: false,
        consentTaskSigned: false,
        manualPaperDisposed: true,
      }),
    ).toEqual([]);
  });

  it("**不可解除**的轉人工（如作廢在途）不受影響", () => {
    const inflightVoid = preview({
      invoice_action: "REVIEW_REQUIRED",
      manual_paper_resolvable: false,
      reason: "作廢在途",
    });
    const blockers = returnSubmitBlockers(inflightVoid, {
      paperRecalled: true,
      consentTaskSigned: true,
      manualPaperDisposed: true,
    });
    expect(blockers).toHaveLength(1);
    expect(blockers[0]).toContain("尚未確認");
  });
});
