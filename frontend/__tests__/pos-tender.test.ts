import { describe, expect, it } from "vitest";

import {
  changeDue,
  resolvePlan,
  toTenders,
  validatePlan,
} from "@/features/pos/tender";

describe("tender 純邏輯", () => {
  it("CASH：全額現金、不需會員、需開帳", () => {
    const plan = resolvePlan("CASH", 1850, 0);
    expect(plan).toEqual({ mode: "CASH", cash: 1850, storeCredit: 0 });
    const v = validatePlan(plan, 1850, {
      hasMember: false,
      memberBalance: null,
    });
    expect(v.ok).toBe(true);
    expect(v.needsDrawer).toBe(true);
    expect(v.needsMember).toBe(false);
  });

  it("STORE_CREDIT：全額購物金、需會員、不需開帳；餘額不足擋", () => {
    const plan = resolvePlan("STORE_CREDIT", 500, 0);
    expect(plan.storeCredit).toBe(500);
    expect(
      validatePlan(plan, 500, { hasMember: true, memberBalance: 500 }).ok,
    ).toBe(true);
    expect(
      validatePlan(plan, 500, { hasMember: false, memberBalance: null }).error,
    ).toMatch(/買方會員/);
    expect(
      validatePlan(plan, 500, { hasMember: true, memberBalance: 300 }).error,
    ).toMatch(/餘額不足/);
    expect(
      validatePlan(plan, 500, { hasMember: true, memberBalance: 500 })
        .needsDrawer,
    ).toBe(false);
  });

  it("購物金餘額未載入（null）時不放行", () => {
    const plan = resolvePlan("STORE_CREDIT", 500, 0);
    const v = validatePlan(plan, 500, { hasMember: true, memberBalance: null });
    expect(v.ok).toBe(false);
    expect(v.error).toMatch(/尚未載入/);
  });

  it("MIXED：現金+購物金須等於 total、兩腿皆 >0", () => {
    const plan = resolvePlan("MIXED", 500, 300);
    expect(plan).toEqual({ mode: "MIXED", cash: 300, storeCredit: 200 });
    expect(
      validatePlan(plan, 500, { hasMember: true, memberBalance: 500 }).ok,
    ).toBe(true);
    // 現金部分等於 total → 購物金腿為 0 → MIXED 不允許
    const allCash = resolvePlan("MIXED", 500, 500);
    expect(
      validatePlan(allCash, 500, { hasMember: true, memberBalance: 500 }).error,
    ).toMatch(/都必須大於 0/);
  });

  it("toTenders：現金/購物金分別產生列；純現金亦明列", () => {
    expect(toTenders(resolvePlan("CASH", 1850, 0))).toEqual([
      { tender_type: "CASH", amount: "1850" },
    ]);
    expect(toTenders(resolvePlan("STORE_CREDIT", 500, 0))).toEqual([
      { tender_type: "STORE_CREDIT", amount: "500" },
    ]);
    expect(toTenders(resolvePlan("MIXED", 500, 300))).toEqual([
      { tender_type: "CASH", amount: "300" },
      { tender_type: "STORE_CREDIT", amount: "200" },
    ]);
  });

  it("changeDue：實收現金 − 應收現金部分", () => {
    expect(changeDue(2000, 1850)).toBe(150);
    expect(changeDue(1800, 1850)).toBe(-50);
  });
});
