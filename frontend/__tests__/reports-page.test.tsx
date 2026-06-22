// @vitest-environment jsdom
// /reports 報表頁測試：MANAGER 權限檢查、購物金報表四分頁渲染、效益指標估計值/代理法標示。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import ReportsPage from "@/app/(authed)/reports/page";
import { clearToken, setToken } from "@/lib/token";

// -- Fixture data: Phase 6 financial reports --

const DAILY_SUMMARY_DATA = {
  store_id: 1,
  date: "2026-06-21",
  gross_turnover: "120000",
  recognized_revenue: "95000",
  gross_margin: "45000",
  gross_margin_rate: "0.4737",
  cogs: "50000",
  consignment_commission_income: "15000",
  unknown_cost_sales: "5000",
  food_revenue: "21000",
  secondhand_revenue: "74000",
  net_sales_ex_tax: "90476",
  tax: "4524",
  total_cash_out: "30000",
  estimated_net_income: "35000",
  estimated_net_income_note: "估計值：未含固定成本",
  avg_ticket: "2400",
  transaction_count: 50,
  store_credit_issued: "8000",
  store_credit_redeemed: "5000",
  cash_variance: "200",
  cash_sales_in: "100000",
  buyout_out: "20000",
  consignment_payout_out: "5000",
  acquisition_void_in: "3000",
  manual_adjust: "2000",
  expected_cash: "80000",
  counted_cash: "80200",
  generated_at: "2026-06-21T18:00:00Z",
};

const TRENDS_DATA = {
  store_id: 1,
  date_from: "2026-05-22T00:00:00Z",
  date_to: "2026-06-21T00:00:00Z",
  granularity: "day",
  rows: [
    {
      period: "2026-06-19",
      gross_turnover: "50000",
      recognized_revenue: "40000",
      gross_margin: "18000",
      gross_margin_rate: "0.45",
      cogs: "22000",
      total_cash_out: "10000",
      transaction_count: 20,
      store_credit_issued: "3000",
      store_credit_redeemed: "1000",
    },
    {
      period: "2026-06-20",
      gross_turnover: "70000",
      recognized_revenue: "55000",
      gross_margin: "27000",
      gross_margin_rate: "0.49",
      cogs: "28000",
      total_cash_out: "15000",
      transaction_count: 30,
      store_credit_issued: "5000",
      store_credit_redeemed: "4000",
    },
  ],
  generated_at: "2026-06-21T18:00:00Z",
};

const DAILY_CASH_DATA = {
  store_id: 1,
  date: "2026-06-21",
  sessions: [
    {
      session_id: 1,
      opened_by: 1,
      opened_at: "2026-06-21T09:00:00Z",
      closed_by: 1,
      closed_at: "2026-06-21T18:00:00Z",
      status: "CLOSED",
      opening_float: "5000",
      cash_sales: "80000",
      buyout_out: "15000",
      consignment_payout_out: "3000",
      sale_refund_out: "2000",
      acquisition_void_in: "1000",
      manual_adjust_total: "500",
      expected_amount: "66500",
      counted_amount: "66300",
      variance: "-200",
    },
  ],
  total_opening_float: "5000",
  total_cash_sales: "80000",
  total_buyout_out: "15000",
  total_consignment_payout_out: "3000",
  total_sale_refund_out: "2000",
  total_acquisition_void_in: "1000",
  total_manual_adjust: "500",
  total_expected: "66500",
  total_counted: "66300",
  total_variance: "-200",
  total_store_credit_redeemed_display_only: "4000",
  generated_at: "2026-06-21T18:00:00Z",
};

const SALES_MARGIN_DATA = {
  store_id: 1,
  date_from: "2026-05-22T00:00:00Z",
  date_to: "2026-06-21T00:00:00Z",
  gross_turnover: "500000",
  recognized_revenue: "400000",
  owned_cogs: "180000",
  bulk_cogs: "50000",
  consignment_commission_income: "60000",
  gross_margin: "170000",
  gross_margin_rate: "0.425",
  unknown_cost_sales: "20000",
  food_revenue: "88000",
  secondhand_revenue: "312000",
  cash_received: "450000",
  store_credit_redeemed: "50000",
  transaction_count: 200,
  generated_at: "2026-06-21T18:00:00Z",
};

const INVENTORY_VALUE_DATA = {
  store_id: 1,
  owned_serialized_count: 50,
  owned_serialized_cost: "250000",
  owned_serialized_retail: "450000",
  owned_bulk_remaining_qty: 120,
  owned_bulk_cost: "60000",
  owned_bulk_retail: "108000",
  total_owned_cost_value: "310000",
  total_owned_retail_value: "558000",
  owned_cost_aging: {
    lt_30d: "180000",
    d30_90: "80000",
    d90_180: "30000",
    d180_365: "15000",
    gt_365d: "5000",
  },
  consignment_serialized_count: 30,
  consignment_bulk_remaining_qty: 40,
  consignment_inventory_gross: "200000",
  catalog_total_qty: 15,
  catalog_retail_value: "75000",
  catalog_cost_value: null,
  generated_at: "2026-06-21T18:00:00Z",
};

const CONSIGNMENT_PAYABLES_DATA = {
  store_id: 1,
  status_filter: "ALL",
  total_pending_payout: "25000",
  total_paid_payout: "80000",
  total_cancelled_payout: "5000",
  total_reclaim_needed_payout: "3000",
  rows: [
    {
      settlement_id: 1,
      sale_id: 10,
      item_code: "SI-001",
      item_name: "Coleman 帳篷",
      consignor_id: 5,
      consignor_name: "王小明",
      consignor_phone: "0912345678",
      gross: "10000",
      commission_amount: "5000",
      payout_amount: "5000",
      status: "PENDING",
      reclaim_needed: false,
      sale_created_at: "2026-06-20T14:00:00Z",
    },
    {
      settlement_id: 2,
      sale_id: 11,
      item_code: "SI-002",
      item_name: "MSR 爐具",
      consignor_id: 6,
      consignor_name: "李大華",
      consignor_phone: "0987654321",
      gross: "8000",
      commission_amount: "4000",
      payout_amount: "4000",
      status: "PAID",
      reclaim_needed: false,
      sale_created_at: "2026-06-19T10:00:00Z",
    },
  ],
  generated_at: "2026-06-21T18:00:00Z",
};

// -- Fixture data (matching generated API types) --

const LIABILITY_DATA = {
  store_id: 1,
  total_outstanding: "58000",
  aging_buckets: {
    lt_30d: "20000",
    d30_90: "15000",
    d90_180: "10000",
    d180_365: "8000",
    gt_365d: "5000",
  },
  per_member: [
    { contact_id: 1, name: "Alice", balance: "30000" },
    { contact_id: 2, name: "Bob", balance: "28000" },
  ],
  liability_health_ratio: "1.45",
  generated_at: "2026-06-18T10:00:00Z",
};

const FLOWS_DATA = {
  store_id: 1,
  date_from: "2026-05-19T00:00:00Z",
  date_to: "2026-06-18T23:59:59Z",
  granularity: "day",
  rows: [
    { period: "2026-06-17", issued: "5000", redeemed: "2000", net_change: "3000" },
  ],
  generated_at: "2026-06-18T10:00:00Z",
};

const EFFECTIVENESS_DATA = {
  store_id: 1,
  date_from: "2026-05-19T00:00:00Z",
  date_to: "2026-06-18T23:59:59Z",
  take_rate: "0.42",
  avg_premium_rate: "0.10",
  beta_retention: "0.35",
  excess_spend_rate: "0.60",
  alpha_incremental: "0.25",
  gross_margin_m: "0.38",
  delta_per_1000: "15",
  redemption_count: 25,
  alpha_sample_insufficient: false,
  estimate_fields: ["beta_retention", "alpha_incremental", "delta_per_1000"],
  alpha_method_note: "代理假設低頻會員消費由購物金誘發。",
  generated_at: "2026-06-18T10:00:00Z",
};

const RECONCILIATION_DATA = {
  store_id: 1,
  ledger_total_outstanding: "58000",
  cached_total_outstanding: "58000",
  cached_total_trustworthy: true,
  mismatches: [],
  generated_at: "2026-06-18T10:00:00Z",
};

// -- Helpers --

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function loginAs(role: "MANAGER" | "CLERK") {
  setToken(fakeJwt({ sub: "1", role, store_id: 1 }));
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubReportsFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      // Financial reports (Phase 6)
      if (url.includes("/reports/daily-summary")) return json(DAILY_SUMMARY_DATA);
      if (url.includes("/reports/trends")) return json(TRENDS_DATA);
      if (url.includes("/reports/daily-cash")) return json(DAILY_CASH_DATA);
      if (url.includes("/reports/sales-margin")) return json(SALES_MARGIN_DATA);
      if (url.includes("/reports/inventory-value")) return json(INVENTORY_VALUE_DATA);
      if (url.includes("/reports/consignment-payables")) return json(CONSIGNMENT_PAYABLES_DATA);
      // Store credit reports
      if (url.includes("/store-credit/liability")) return json(LIABILITY_DATA);
      if (url.includes("/store-credit/flows")) return json(FLOWS_DATA);
      if (url.includes("/store-credit/effectiveness")) return json(EFFECTIVENESS_DATA);
      if (url.includes("/store-credit/reconciliation")) return json(RECONCILIATION_DATA);
      throw new Error(`unmatched fetch: ${url}`);
    }),
  );
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<ReportsPage />, { wrapper });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearToken();
});

describe("ReportsPage", () => {
  it("backend 403 sees permission notice (server-driven gate)", async () => {
    loginAs("CLERK");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ detail: "權限不足" }, 403)),
    );
    renderPage();
    expect(await screen.findByText("需管理者權限")).toBeTruthy();
  });

  it("access probe server error (500) → load error, NOT permission notice", async () => {
    loginAs("MANAGER");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ detail: "boom" }, 500)),
    );
    renderPage();
    expect(await screen.findByText("無法連線報表服務，請稍後再試")).toBeTruthy();
    expect(screen.queryByText("需管理者權限")).toBeNull();
  });

  it("stale CLERK token but backend authorizes (promoted) → renders page, not blocked", async () => {
    // 永不過期 token 的 role claim 可能過時：token 說 CLERK 但後端已升權 → 應看得到報表。
    loginAs("CLERK");
    stubReportsFetch();
    renderPage();
    // gate 以後端授權為準，故即使 token=CLERK 也應渲染 dashboard 資料、不顯示權限提示。
    expect(await screen.findByText("120,000")).toBeTruthy();
    expect(screen.queryByText("需管理者權限")).toBeNull();
  });

  it("manager sees liability tab with totals and per-member table", async () => {
    loginAs("MANAGER");
    stubReportsFetch();
    renderPage();
    // Default tab is now dashboard; navigate to liability
    await screen.findByText("120,000"); // wait for dashboard
    await userEvent.click(screen.getByRole("tab", { name: "負債" }));

    // total outstanding
    expect(await screen.findByText("58,000")).toBeTruthy();

    // per-member names
    expect(screen.getByText("Alice")).toBeTruthy();
    expect(screen.getByText("Bob")).toBeTruthy();

    // aging buckets
    expect(screen.getByText("20,000")).toBeTruthy(); // lt_30d

    // liability health ratio
    expect(screen.getByText("1.45")).toBeTruthy();
  });

  it("flows tab shows period/issued/redeemed/net_change", async () => {
    loginAs("MANAGER");
    stubReportsFetch();
    renderPage();
    await screen.findByText("120,000"); // wait for dashboard to load first

    await userEvent.click(screen.getByRole("tab", { name: "流量" }));
    expect(await screen.findByText("2026-06-17")).toBeTruthy();
    expect(screen.getByText("5,000")).toBeTruthy();
    expect(screen.getByText("2,000")).toBeTruthy();
    expect(screen.getByText("3,000")).toBeTruthy();
  });

  it("effectiveness tab shows estimate labels and alpha proxy note", async () => {
    loginAs("MANAGER");
    stubReportsFetch();
    renderPage();
    await screen.findByText("120,000");

    await userEvent.click(screen.getByRole("tab", { name: "效益指標" }));

    // Wait for effectiveness data
    await waitFor(() => {
      expect(screen.getByText("選用率")).toBeTruthy();
    });

    // estimate_fields should be labelled
    const betaRow = screen.getByText("沉澱率 (beta)").closest("tr") ?? screen.getByText("沉澱率 (beta)").parentElement;
    expect(betaRow?.textContent).toContain("估計值");

    const alphaRow = screen.getByText("新增比例 (alpha)").closest("tr") ?? screen.getByText("新增比例 (alpha)").parentElement;
    expect(alphaRow?.textContent).toContain("估計值");
    expect(alphaRow?.textContent).toContain("代理法");

    // alpha_method_note displayed
    expect(screen.getByText(/代理假設低頻會員消費由購物金誘發/)).toBeTruthy();
  });

  it("effectiveness tab shows 'sample insufficient' note when flagged", async () => {
    loginAs("MANAGER");

    const insufficientData = {
      ...EFFECTIVENESS_DATA,
      alpha_sample_insufficient: true,
    };

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("/reports/daily-summary")) return json(DAILY_SUMMARY_DATA);
        if (url.includes("/reports/trends")) return json(TRENDS_DATA);
        if (url.includes("/reports/daily-cash")) return json(DAILY_CASH_DATA);
        if (url.includes("/reports/sales-margin")) return json(SALES_MARGIN_DATA);
        if (url.includes("/reports/inventory-value")) return json(INVENTORY_VALUE_DATA);
        if (url.includes("/reports/consignment-payables")) return json(CONSIGNMENT_PAYABLES_DATA);
        if (url.includes("/store-credit/liability")) return json(LIABILITY_DATA);
        if (url.includes("/store-credit/flows")) return json(FLOWS_DATA);
        if (url.includes("/store-credit/effectiveness")) return json(insufficientData);
        if (url.includes("/store-credit/reconciliation")) return json(RECONCILIATION_DATA);
        throw new Error(`unmatched fetch: ${url}`);
      }),
    );

    renderPage();
    await screen.findByText("120,000");
    await userEvent.click(screen.getByRole("tab", { name: "效益指標" }));

    await waitFor(() => {
      expect(screen.getByText("樣本不足")).toBeTruthy();
    });
  });

  it("reconciliation tab shows ledger vs cached totals", async () => {
    loginAs("MANAGER");
    stubReportsFetch();
    renderPage();
    await screen.findByText("120,000");

    await userEvent.click(screen.getByRole("tab", { name: "對帳" }));
    await waitFor(() => {
      // Both ledger and cached are 58,000 - there will be multiple
      const moneyElements = screen.getAllByText("58,000");
      expect(moneyElements.length).toBeGreaterThanOrEqual(2); // at least ledger + cached
    });
  });

  it("reconciliation tab exports CSV with Authorization header", async () => {
    loginAs("MANAGER");
    const downloads: { url: string; auth: string | null }[] = [];
    const origCreate = URL.createObjectURL;
    const origRevoke = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn(() => "blob:mock");
    URL.revokeObjectURL = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("format=csv") || url.includes("format=xlsx")) {
          const headers = new Headers(
            init?.headers ?? (input instanceof Request ? input.headers : undefined),
          );
          downloads.push({ url, auth: headers.get("Authorization") });
          return new Response("a,b\n1,2", { status: 200 });
        }
        if (url.includes("/reports/daily-summary")) return json(DAILY_SUMMARY_DATA);
        if (url.includes("/reports/trends")) return json(TRENDS_DATA);
        if (url.includes("/reports/daily-cash")) return json(DAILY_CASH_DATA);
        if (url.includes("/reports/sales-margin")) return json(SALES_MARGIN_DATA);
        if (url.includes("/reports/inventory-value")) return json(INVENTORY_VALUE_DATA);
        if (url.includes("/reports/consignment-payables")) return json(CONSIGNMENT_PAYABLES_DATA);
        if (url.includes("/store-credit/liability")) return json(LIABILITY_DATA);
        if (url.includes("/store-credit/flows")) return json(FLOWS_DATA);
        if (url.includes("/store-credit/effectiveness")) return json(EFFECTIVENESS_DATA);
        if (url.includes("/store-credit/reconciliation")) return json(RECONCILIATION_DATA);
        throw new Error(`unmatched fetch: ${url}`);
      }),
    );
    renderPage();
    // Default tab is now "dashboard", wait for it
    await screen.findByText("120,000");
    await userEvent.click(screen.getByRole("tab", { name: "對帳" }));
    await screen.findByText("所有帳戶一致，無異常。");

    await userEvent.click(screen.getByRole("button", { name: "CSV" }));
    await waitFor(() => expect(downloads).toHaveLength(1));
    expect(downloads[0].url).toContain("/store-credit/reconciliation");
    expect(downloads[0].url).toContain("format=csv");
    expect(downloads[0].auth).toMatch(/^Bearer /);

    URL.createObjectURL = origCreate;
    URL.revokeObjectURL = origRevoke;
  });

  // -- Phase 6 Financial Reports Tests --

  it("default tab is dashboard (today's operations), shows key KPIs", async () => {
    loginAs("MANAGER");
    stubReportsFetch();
    renderPage();

    // gross_turnover
    expect(await screen.findByText("120,000")).toBeTruthy();
    // recognized_revenue
    expect(screen.getByText("95,000")).toBeTruthy();
    // gross_margin
    expect(screen.getByText("45,000")).toBeTruthy();
    // transaction_count
    expect(screen.getByText("50")).toBeTruthy();
    // avg_ticket
    expect(screen.getByText("2,400")).toBeTruthy();
    // estimated_net_income
    expect(screen.getByText("35,000")).toBeTruthy();
    // 餐飲/二手分列
    expect(screen.getByText("74,000")).toBeTruthy(); // secondhand_revenue
    expect(screen.getByText("21,000")).toBeTruthy(); // food_revenue
    // estimated_net_income_note shown in footnote (prefixed by 估算淨利說明：, alongside 估計值 badge)
    expect(
      screen.getByText(
        (_, el) =>
          el?.tagName === "P" &&
          el.textContent?.includes(DAILY_SUMMARY_DATA.estimated_net_income_note) === true,
      ),
    ).toBeTruthy();
  });

  it("trends tab shows data table and has granularity selector", async () => {
    loginAs("MANAGER");
    stubReportsFetch();
    renderPage();
    await screen.findByText("120,000"); // wait for dashboard

    await userEvent.click(screen.getByRole("tab", { name: "趨勢" }));
    // Wait for trend data - periods appear in both SVG chart and data table
    await waitFor(() => {
      expect(screen.getAllByText("2026-06-19").length).toBeGreaterThanOrEqual(1);
    });
    expect(screen.getAllByText("2026-06-20").length).toBeGreaterThanOrEqual(1);

    // Recognized revenue values may appear in both chart ticks and table; just verify presence
    expect(screen.getAllByText("40,000").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("55,000").length).toBeGreaterThanOrEqual(1);

    // Granularity selector exists (look within select elements)
    const selects = screen.getAllByRole("combobox");
    const granularitySelect = selects.find((s) => (s as HTMLSelectElement).value === "day");
    expect(granularitySelect).toBeTruthy();
  });

  it("daily cash tab shows session table and totals", async () => {
    loginAs("MANAGER");
    stubReportsFetch();
    renderPage();
    await screen.findByText("120,000");

    await userEvent.click(screen.getByRole("tab", { name: "現金對帳" }));
    // Total expected appears in both session row and totals
    await waitFor(() => {
      expect(screen.getAllByText("66,500").length).toBeGreaterThanOrEqual(2);
    });
    // Total variance appears in both session row and totals
    expect(screen.getAllByText("-200").length).toBeGreaterThanOrEqual(2);
    // Session status
    expect(screen.getByText("CLOSED")).toBeTruthy();
  });

  it("sales margin tab shows margin metrics", async () => {
    loginAs("MANAGER");
    stubReportsFetch();
    renderPage();
    await screen.findByText("120,000");

    await userEvent.click(screen.getByRole("tab", { name: "銷售毛利" }));
    // gross_turnover
    expect(await screen.findByText("500,000")).toBeTruthy();
    // recognized_revenue
    expect(screen.getByText("400,000")).toBeTruthy();
    // gross_margin
    expect(screen.getByText("170,000")).toBeTruthy();
    // 餐飲/二手分列
    expect(screen.getByText("88,000")).toBeTruthy(); // food_revenue
    expect(screen.getByText("312,000")).toBeTruthy(); // secondhand_revenue
    // transaction_count
    expect(screen.getByText("200")).toBeTruthy();
  });

  it("inventory value tab shows owned/consignment/catalog sections", async () => {
    loginAs("MANAGER");
    stubReportsFetch();
    renderPage();
    await screen.findByText("120,000");

    await userEvent.click(screen.getByRole("tab", { name: "庫存價值" }));
    // total owned cost
    expect(await screen.findByText("310,000")).toBeTruthy();
    // total owned retail
    expect(screen.getByText("558,000")).toBeTruthy();
    // consignment gross
    expect(screen.getByText("200,000")).toBeTruthy();
    // catalog retail
    expect(screen.getByText("75,000")).toBeTruthy();
  });

  it("consignment payables tab shows totals and detail rows", async () => {
    loginAs("MANAGER");
    stubReportsFetch();
    renderPage();
    await screen.findByText("120,000");

    await userEvent.click(screen.getByRole("tab", { name: "寄售應付" }));
    // Total pending
    expect(await screen.findByText("25,000")).toBeTruthy();
    // Total paid
    expect(screen.getByText("80,000")).toBeTruthy();
    // Consignor names (no national_id)
    expect(screen.getByText("Coleman 帳篷")).toBeTruthy();
    expect(screen.getByText("MSR 爐具")).toBeTruthy();
    expect(screen.getByText("王小明")).toBeTruthy();
    expect(screen.getByText("李大華")).toBeTruthy();
    // status filter exists
    const statusFilter = screen.getByDisplayValue("ALL");
    expect(statusFilter).toBeTruthy();
  });

  it("dashboard tab has CSV and XLSX download buttons", async () => {
    loginAs("MANAGER");
    stubReportsFetch();
    renderPage();
    await screen.findByText("120,000");

    // Download buttons present on dashboard
    const csvButtons = screen.getAllByRole("button", { name: "CSV" });
    const xlsxButtons = screen.getAllByRole("button", { name: "Excel" });
    expect(csvButtons.length).toBeGreaterThanOrEqual(1);
    expect(xlsxButtons.length).toBeGreaterThanOrEqual(1);
  });

  it("store credit tabs still accessible from new tabbed layout", async () => {
    loginAs("MANAGER");
    stubReportsFetch();
    renderPage();
    await screen.findByText("120,000"); // dashboard loads

    // Navigate to store credit liability tab
    await userEvent.click(screen.getByRole("tab", { name: "負債" }));
    expect(await screen.findByText("58,000")).toBeTruthy();
    expect(screen.getByText("Alice")).toBeTruthy();
  });
});
