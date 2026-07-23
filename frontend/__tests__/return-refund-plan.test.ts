import { describe, expect, it } from "vitest";

import { refundPlan, supportsRefund } from "@/features/returns/refund";

describe("退貨退款拆帳預估", () => {
  const tenders = [
    { tender_type: "STORE_CREDIT" as const, amount: "300" },
    { tender_type: "LINE_PAY" as const, amount: "700" },
  ];

  it("購物金優先：先退 200 不碰 LINE Pay", () => {
    expect(refundPlan(tenders, 0, 200)).toEqual([
      { tender_type: "STORE_CREDIT", amount: 200 },
    ]);
  });

  it("累計跨過購物金額度時，只退本次 LINE Pay 差額", () => {
    expect(refundPlan(tenders, 200, 200)).toEqual([
      { tender_type: "STORE_CREDIT", amount: 100 },
      { tender_type: "LINE_PAY", amount: 100 },
    ]);
  });

  it("購物金＋台灣Pay沿用同一規則", () => {
    expect(
      refundPlan(
        [
          { tender_type: "STORE_CREDIT", amount: "300" },
          { tender_type: "TAIWAN_PAY", amount: "100" },
        ],
        200,
        200,
      ),
    ).toEqual([
      { tender_type: "STORE_CREDIT", amount: 100 },
      { tender_type: "TAIWAN_PAY", amount: 100 },
    ]);
  });

  it("不支援未包含購物金的多外部付款退款", () => {
    const cashLine = [
      { tender_type: "CASH" as const, amount: "300" },
      { tender_type: "LINE_PAY" as const, amount: "700" },
    ];
    expect(supportsRefund(cashLine)).toBe(false);
    expect(refundPlan(cashLine, 0, 200)).toEqual([]);
  });
});
