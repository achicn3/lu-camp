import { describe, expect, it } from "vitest";

import {
  changeDue,
  resolvePlan,
  toTenders,
  validatePlan,
} from "@/features/pos/tender";

const OPEN = { drawerOpen: true } as const;

describe("tender 純邏輯", () => {
  it("CASH：全額現金、不需會員、需開帳", () => {
    const plan = resolvePlan("CASH", 1850, 0);
    expect(plan).toEqual({ mode: "CASH", cash: 1850, storeCredit: 0 });
    const v = validatePlan(plan, 1850, {
      hasMember: false,
      memberBalance: null,
      ...OPEN,
    });
    expect(v.ok).toBe(true);
    expect(v.needsDrawer).toBe(true);
    expect(v.needsMember).toBe(false);
  });

  it("CASH 未開帳 / 開帳狀態未知 → 擋", () => {
    const plan = resolvePlan("CASH", 100, 0);
    expect(
      validatePlan(plan, 100, {
        hasMember: false,
        memberBalance: null,
        drawerOpen: false,
      }).error,
    ).toMatch(/需先開帳/);
    expect(
      validatePlan(plan, 100, {
        hasMember: false,
        memberBalance: null,
        drawerOpen: null,
      }).error,
    ).toMatch(/讀取開帳狀態/);
  });

  it("STORE_CREDIT：全額購物金、需會員、不需開帳；餘額不足擋", () => {
    const plan = resolvePlan("STORE_CREDIT", 500, 0);
    expect(plan.storeCredit).toBe(500);
    // 純購物金不需開帳：drawerOpen=false 也應放行
    expect(
      validatePlan(plan, 500, {
        hasMember: true,
        memberBalance: 500,
        drawerOpen: false,
      }).ok,
    ).toBe(true);
    expect(
      validatePlan(plan, 500, {
        hasMember: false,
        memberBalance: null,
        ...OPEN,
      }).error,
    ).toMatch(/買方會員/);
    expect(
      validatePlan(plan, 500, {
        hasMember: true,
        memberBalance: 300,
        ...OPEN,
      }).error,
    ).toMatch(/餘額不足/);
  });

  it("購物金餘額未載入（null）時不放行", () => {
    const plan = resolvePlan("STORE_CREDIT", 500, 0);
    const v = validatePlan(plan, 500, {
      hasMember: true,
      memberBalance: null,
      ...OPEN,
    });
    expect(v.ok).toBe(false);
    expect(v.error).toMatch(/尚未載入/);
  });

  it("MIXED：現金+購物金須等於 total、兩腿皆 >0", () => {
    const plan = resolvePlan("MIXED", 500, 300);
    expect(plan).toEqual({ mode: "MIXED", cash: 300, storeCredit: 200 });
    expect(
      validatePlan(plan, 500, { hasMember: true, memberBalance: 500, ...OPEN })
        .ok,
    ).toBe(true);
    // 現金部分等於 total → 購物金腿為 0 → MIXED 不允許
    const allCash = resolvePlan("MIXED", 500, 500);
    expect(
      validatePlan(allCash, 500, {
        hasMember: true,
        memberBalance: 500,
        ...OPEN,
      }).error,
    ).toMatch(/都必須大於 0/);
  });

  it("storeCreditMax：內用餐飲不可用購物金折抵（購物金 > 上限 → 擋）", () => {
    // total=380、餐飲=180 → store_credit_max=200。購物金 300 > 200 → 擋。
    const over = resolvePlan("MIXED", 380, 80);
    const v = validatePlan(over, 380, {
      hasMember: true,
      memberBalance: 1000,
      storeCreditMax: 200,
      ...OPEN,
    });
    expect(v.ok).toBe(false);
    expect(v.error).toMatch(/內用餐飲不可用購物金折抵/);
    // 購物金 200（=上限）OK。
    const okPlan = resolvePlan("MIXED", 380, 180);
    expect(
      validatePlan(okPlan, 380, {
        hasMember: true,
        memberBalance: 1000,
        storeCreditMax: 200,
        ...OPEN,
      }).ok,
    ).toBe(true);
  });

  it("storeCreditMinSpend：非餐飲消費未達低消門檻 → 完全不可用購物金", () => {
    // total=300（皆非餐飲）、store_credit_max=300，但低消門檻 500 → 不可用購物金。
    const v = validatePlan(resolvePlan("STORE_CREDIT", 300, 0), 300, {
      hasMember: true,
      memberBalance: 1000,
      storeCreditMax: 300,
      storeCreditMinSpend: 500,
      ...OPEN,
    });
    expect(v.ok).toBe(false);
    expect(v.error).toMatch(/未達購物金低消/);
    // 門檻 0（預設）→ 不限制，可用。
    expect(
      validatePlan(resolvePlan("STORE_CREDIT", 300, 0), 300, {
        hasMember: true,
        memberBalance: 1000,
        storeCreditMax: 300,
        storeCreditMinSpend: 0,
        ...OPEN,
      }).ok,
    ).toBe(true);
    // 達門檻（非餐飲 500 = 門檻 500）→ 可用。
    expect(
      validatePlan(resolvePlan("STORE_CREDIT", 500, 0), 500, {
        hasMember: true,
        memberBalance: 1000,
        storeCreditMax: 500,
        storeCreditMinSpend: 500,
        ...OPEN,
      }).ok,
    ).toBe(true);
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
