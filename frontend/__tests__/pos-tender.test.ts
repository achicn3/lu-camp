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
    expect(plan).toEqual({
      mode: "CASH",
      cash: 1850,
      storeCredit: 0,
      taiwanPay: 0,
      linePay: 0,
    });
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

  it("STORE_CREDIT：餘額可高於商品金額；只扣應付額、需會員、不需開帳", () => {
    const plan = resolvePlan("STORE_CREDIT", 500, 0);
    expect(plan.storeCredit).toBe(500);
    // 純購物金不需開帳：drawerOpen=false 也應放行
    expect(
      validatePlan(plan, 500, {
        hasMember: true,
        memberBalance: 1000,
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

  it("TAIWAN_PAY：全額台灣Pay、非現金（不需會員、不需開帳）", () => {
    const plan = resolvePlan("TAIWAN_PAY", 680, 0);
    expect(plan).toEqual({
      mode: "TAIWAN_PAY",
      cash: 0,
      storeCredit: 0,
      taiwanPay: 680,
      linePay: 0,
    });
    // 非現金：drawerOpen=false（未開帳）也放行；不需會員。
    const v = validatePlan(plan, 680, {
      hasMember: false,
      memberBalance: null,
      drawerOpen: false,
      taiwanPayConfirmed: true,
    });
    expect(v.ok).toBe(true);
    expect(v.needsDrawer).toBe(false);
    expect(v.needsMember).toBe(false);
  });

  it("LINE_PAY：全額 LINE Pay、非現金、需先掃付款碼才放行", () => {
    const plan = resolvePlan("LINE_PAY", 900, 0);
    expect(plan).toEqual({
      mode: "LINE_PAY",
      cash: 0,
      storeCredit: 0,
      taiwanPay: 0,
      linePay: 900,
    });
    // 未掃碼 → 擋
    const noKey = validatePlan(plan, 900, {
      hasMember: false,
      memberBalance: null,
      drawerOpen: false,
    });
    expect(noKey.ok).toBe(false);
    expect(noKey.error).toMatch(/掃描客人的 LINE Pay/);
    // 掃到碼 → 放行（非現金、不需開帳/會員）
    const withKey = validatePlan(plan, 900, {
      hasMember: false,
      memberBalance: null,
      drawerOpen: false,
      linePayKey: "OTK-123",
    });
    expect(withKey.ok).toBe(true);
    expect(withKey.needsDrawer).toBe(false);
    expect(withKey.needsMember).toBe(false);
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
    const plan = resolvePlan("MIXED", 500, 200);
    expect(plan).toEqual({
      mode: "MIXED",
      cash: 300,
      storeCredit: 200,
      taiwanPay: 0,
      linePay: 0,
    });
    expect(
      validatePlan(plan, 500, { hasMember: true, memberBalance: 500, ...OPEN })
        .ok,
    ).toBe(true);
    // 現金部分等於 total → 購物金腿為 0 → MIXED 不允許
    const allCash = resolvePlan("MIXED", 500, 0);
    expect(
      validatePlan(allCash, 500, {
        hasMember: true,
        memberBalance: 500,
        ...OPEN,
      }).error,
    ).toMatch(/都必須大於 0/);
  });

  it("MIXED：輸入購物金後可將剩餘金額交由現金、LINE Pay 或台灣Pay", () => {
    expect(resolvePlan("MIXED", 1000, 300, "CASH")).toEqual({
      mode: "MIXED",
      cash: 700,
      storeCredit: 300,
      taiwanPay: 0,
      linePay: 0,
    });
    expect(resolvePlan("MIXED", 1000, 300, "LINE_PAY")).toEqual({
      mode: "MIXED",
      cash: 0,
      storeCredit: 300,
      taiwanPay: 0,
      linePay: 700,
    });
    expect(resolvePlan("MIXED", 1000, 300, "TAIWAN_PAY")).toEqual({
      mode: "MIXED",
      cash: 0,
      storeCredit: 300,
      taiwanPay: 700,
      linePay: 0,
    });
  });

  it("MIXED：購物金＋行動支付需要會員但不需要開帳", () => {
    const linePay = validatePlan(
      resolvePlan("MIXED", 1000, 300, "LINE_PAY"),
      1000,
      {
        hasMember: true,
        memberBalance: 500,
        drawerOpen: false,
        linePayKey: "OTK-123",
      },
    );
    expect(linePay).toEqual({
      ok: true,
      error: null,
      needsMember: true,
      needsDrawer: false,
    });

    const taiwanPay = validatePlan(
      resolvePlan("MIXED", 1000, 300, "TAIWAN_PAY"),
      1000,
      {
        hasMember: true,
        memberBalance: 500,
        drawerOpen: false,
        taiwanPayConfirmed: true,
      },
    );
    expect(taiwanPay).toEqual({
      ok: true,
      error: null,
      needsMember: true,
      needsDrawer: false,
    });
  });

  it("台灣Pay尚未確認實際收款時不可完成結帳", () => {
    const v = validatePlan(resolvePlan("TAIWAN_PAY", 680, 0), 680, {
      hasMember: false,
      memberBalance: null,
      drawerOpen: false,
      taiwanPayConfirmed: false,
    });
    expect(v.ok).toBe(false);
    expect(v.error).toMatch(/確認已於台灣Pay收到 680 元/);
  });

  it("storeCreditMax：內用餐飲不可用購物金折抵（購物金 > 上限 → 擋）", () => {
    // total=380、餐飲=180 → store_credit_max=200。購物金 300 > 200 → 擋。
    const over = resolvePlan("MIXED", 380, 300);
    const v = validatePlan(over, 380, {
      hasMember: true,
      memberBalance: 1000,
      storeCreditMax: 200,
      ...OPEN,
    });
    expect(v.ok).toBe(false);
    expect(v.error).toMatch(/內用餐飲不可用購物金折抵/);
    // 購物金 200（=上限）OK。
    const okPlan = resolvePlan("MIXED", 380, 200);
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
    expect(toTenders(resolvePlan("MIXED", 500, 200))).toEqual([
      { tender_type: "CASH", amount: "300" },
      { tender_type: "STORE_CREDIT", amount: "200" },
    ]);
    expect(toTenders(resolvePlan("TAIWAN_PAY", 680, 0))).toEqual([
      { tender_type: "TAIWAN_PAY", amount: "680" },
    ]);
    // LINE Pay 帶掃到的一次性付款碼
    expect(
      toTenders(resolvePlan("LINE_PAY", 900, 0), { linePayKey: " OTK-123 " }),
    ).toEqual([
      {
        tender_type: "LINE_PAY",
        amount: "900",
        line_pay_one_time_key: "OTK-123",
      },
    ]);
  });

  it("changeDue：實收現金 − 應收現金部分", () => {
    expect(changeDue(2000, 1850)).toBe(150);
    expect(changeDue(1800, 1850)).toBe(-50);
  });
});

describe("空車 vs 折後零元（Codex 波次三 P2）", () => {
  it("真空車（無品項）total<=0 → 中性、不回錯誤", () => {
    const plan = resolvePlan("CASH", 0, 0);
    const v = validatePlan(plan, 0, {
      hasMember: false,
      memberBalance: null,
      drawerOpen: true,
      cartHasItems: false,
    });
    expect(v.ok).toBe(false);
    expect(v.error).toBeNull();
  });

  it("非空車折後總額 0（如 $1 套 99% 折）→ 可行動錯誤、不靜默", () => {
    const plan = resolvePlan("CASH", 0, 0);
    const v = validatePlan(plan, 0, {
      hasMember: false,
      memberBalance: null,
      drawerOpen: true,
      cartHasItems: true,
    });
    expect(v.ok).toBe(false);
    expect(v.error).toMatch(/折後總額為 0/);
  });
});
