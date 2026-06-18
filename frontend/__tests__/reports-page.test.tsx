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

  it("stale CLERK token but backend authorizes (promoted) → renders page, not blocked", async () => {
    // 永不過期 token 的 role claim 可能過時：token 說 CLERK 但後端已升權 → 應看得到報表。
    loginAs("CLERK");
    stubReportsFetch();
    renderPage();
    // gate 以後端授權為準，故即使 token=CLERK 也應渲染負債資料、不顯示權限提示。
    expect(await screen.findByText("58,000")).toBeTruthy();
    expect(screen.queryByText("需管理者權限")).toBeNull();
  });

  it("manager sees liability tab with totals and per-member table", async () => {
    loginAs("MANAGER");
    stubReportsFetch();
    renderPage();

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
    await screen.findByText("58,000"); // wait for liability to load first

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
    await screen.findByText("58,000");

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
        if (url.includes("/store-credit/liability")) return json(LIABILITY_DATA);
        if (url.includes("/store-credit/flows")) return json(FLOWS_DATA);
        if (url.includes("/store-credit/effectiveness")) return json(insufficientData);
        if (url.includes("/store-credit/reconciliation")) return json(RECONCILIATION_DATA);
        throw new Error(`unmatched fetch: ${url}`);
      }),
    );

    renderPage();
    await screen.findByText("58,000");
    await userEvent.click(screen.getByRole("tab", { name: "效益指標" }));

    await waitFor(() => {
      expect(screen.getByText("樣本不足")).toBeTruthy();
    });
  });

  it("reconciliation tab shows ledger vs cached totals", async () => {
    loginAs("MANAGER");
    stubReportsFetch();
    renderPage();
    await screen.findByText("58,000");

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
        if (url.includes("/store-credit/liability")) return json(LIABILITY_DATA);
        if (url.includes("/store-credit/flows")) return json(FLOWS_DATA);
        if (url.includes("/store-credit/effectiveness")) return json(EFFECTIVENESS_DATA);
        if (url.includes("/store-credit/reconciliation")) return json(RECONCILIATION_DATA);
        throw new Error(`unmatched fetch: ${url}`);
      }),
    );
    renderPage();
    await screen.findByText("58,000");
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
});
