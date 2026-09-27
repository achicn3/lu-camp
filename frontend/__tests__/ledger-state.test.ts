import { describe, expect, it } from "vitest";

import { ledgerStateFor } from "@/features/customer-display/checkout/ledgerState";

const base = { status: "DRAFT" as const, completed: false, paymentFailed: false, leaving: false, pen: null };

describe("結帳手帳動畫狀態：POS 狀態永遠優先", () => {
  it("一般核對時看筆在做什麼", () => {
    expect(ledgerStateFor(base)).toBe("CHECKOUT");
    expect(ledgerStateFor({ ...base, pen: "ITEM_ADD" })).toBe("ITEM_ADD");
    expect(ledgerStateFor({ ...base, pen: "ITEM_DELETE" })).toBe("ITEM_DELETE");
  });

  it("付款中、付款成功會蓋過還在寫的商品動畫", () => {
    expect(ledgerStateFor({ ...base, status: "PROCESSING", pen: "ITEM_ADD" })).toBe("PAYMENT_PENDING");
    expect(ledgerStateFor({ ...base, status: "COMPLETED", pen: "ITEM_DELETE" })).toBe("PAYMENT_SUCCESS");
  });

  it("付款退回可修改＝付款失敗；整筆取消優先於一切", () => {
    expect(ledgerStateFor({ ...base, paymentFailed: true })).toBe("PAYMENT_FAILED");
    expect(ledgerStateFor({ ...base, status: "COMPLETED", leaving: true })).toBe("CHECKOUT_CANCELLED");
  });
});
