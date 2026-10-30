// 採購單日期（docs/99 §5、1B）：付款條件的白話說明、預計到貨是否已逾期。
import { describe, expect, it } from "vitest";

import { isLateDelivery, paymentTermsLabel } from "@/features/purchasing/purchasing";

describe("付款條件的白話說明", () => {
  it("四種條件與沒設定", () => {
    expect(paymentTermsLabel(null, null)).toBe("未設定");
    expect(paymentTermsLabel("PREPAID", null)).toBe("先付（下單就付）");
    expect(paymentTermsLabel("COD", null)).toBe("貨到付款");
    expect(paymentTermsLabel("MONTHLY", 10)).toBe("月結（每月 10 號付上個月的）");
    expect(paymentTermsLabel("NET_DAYS", 30)).toBe("到貨後 30 天內付");
  });
});

describe("逾期未到", () => {
  it("還在等貨、而且預計日已經過了才算", () => {
    expect(isLateDelivery({ status: "ORDERED", expected_on: "2026-11-05" }, "2026-11-10")).toBe(true);
    expect(isLateDelivery({ status: "PARTIAL", expected_on: "2026-11-09" }, "2026-11-10")).toBe(true);
    expect(isLateDelivery({ status: "ORDERED", expected_on: "2026-11-10" }, "2026-11-10")).toBe(false);
    expect(isLateDelivery({ status: "RECEIVED", expected_on: "2026-11-05" }, "2026-11-10")).toBe(false);
    expect(isLateDelivery({ status: "DRAFT", expected_on: "2026-11-05" }, "2026-11-10")).toBe(false);
    expect(isLateDelivery({ status: "ORDERED", expected_on: null }, "2026-11-10")).toBe(false);
  });
});

describe("供應商付款條件表單", () => {
  it("轉成 API 欄位；月結、到貨後要填數字", async () => {
    const { termsPayload } = await import("@/features/purchasing/purchasing");
    expect(termsPayload("", "")).toEqual({ payment_terms: null, payment_terms_value: null });
    expect(termsPayload("COD", "")).toEqual({ payment_terms: "COD", payment_terms_value: null });
    expect(termsPayload("MONTHLY", "10")).toEqual({ payment_terms: "MONTHLY", payment_terms_value: 10 });
    expect(termsPayload("NET_DAYS", "0")).toEqual({ payment_terms: "NET_DAYS", payment_terms_value: 0 });
    expect(termsPayload("MONTHLY", "32")).toEqual({ error: "月結要填每月幾號付（1 到 31）" });
    expect(termsPayload("MONTHLY", "")).toEqual({ error: "月結要填每月幾號付（1 到 31）" });
    expect(termsPayload("NET_DAYS", "366")).toEqual({ error: "到貨後幾天付要填 0 到 365" });
    expect(termsPayload("NET_DAYS", "1.5")).toEqual({ error: "到貨後幾天付要填 0 到 365" });
  });
});
