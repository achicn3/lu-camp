// @vitest-environment jsdom
// /settings 設定頁測試：MANAGER 可見完整設定、溢價建議值採納、PATCH 僅送變更欄位、
// 非 MANAGER 顯示權限不足提示。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import SettingsPage from "@/app/(authed)/settings/page";
import { clearToken, setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function loginAs(role: "MANAGER" | "CLERK") {
  setToken(fakeJwt({ sub: "1", role, store_id: 1 }));
}

const SETTINGS = {
  store_id: 1,
  einvoice_enabled: true,
  tax_rate: "0.0500",
  default_commission_pct: 50,
  default_margin_pct: 45,
  premium_rate: "0.1000",
  premium_rate_min: "0.0000",
  premium_rate_max: "0.2000",
  monthly_fixed_cash_outflow: "50000",
  store_credit_min_spend: "0",
  store_credit_engine_params: {},
  allow_clerk_manage_categories: false,
};

const SUGGESTION = {
  store_id: 1,
  for_date: "2026-06-18",
  suggested_rate: "0.1250",
  insufficient_data: false,
  engine_version: "v1",
  window_metrics: {
    combined: { take_rate: 0.45, avg_premium_rate: 0.1 },
    liability_ratio: 1.2,
    current_rate: 0.1,
  },
  constraint_values: {
    p_max1: "0.1500",
    p_max2: "0.2000",
    p_max2_note: null,
    take_rate_directional: "0.1250",
    combined_take_rate: 0.45,
    combined_alpha: 0.3,
    combined_margin: 0.42,
  },
};

const HISTORY = [
  {
    id: 1,
    old_rate: "0.0800",
    new_rate: "0.1000",
    changed_by: 1,
    changed_at: "2026-06-10T10:00:00Z",
    suggested_rate_at_change: "0.0900",
    reason: "調高溢價率",
  },
];

type FetchRoute = (url: string, init?: RequestInit) => Response | null;

function stubFetch(route: FetchRoute) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      const body =
        input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
      const resp = route(url, { method, body } as RequestInit);
      if (resp) return resp;
      throw new Error(`unmatched fetch: ${method} ${url}`);
    }),
  );
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<SettingsPage />, { wrapper });
}

function defaultStub(overrides?: Partial<{ settings: unknown; suggestion: unknown; history: unknown }>) {
  stubFetch((url) => {
    if (url.includes("/settings/premium-rate/history")) return json(overrides?.history ?? HISTORY);
    if (url.includes("/settings")) return json(overrides?.settings ?? SETTINGS);
    if (url.includes("/premium-suggestion/today")) return json(overrides?.suggestion ?? SUGGESTION);
    return null;
  });
}

afterEach(() => {
  cleanup();
  clearToken();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("/settings", () => {
  it("後端 403（manager-only 歷史端點）顯示權限不足提示（server-driven gate）", async () => {
    loginAs("CLERK");
    // /settings 與 premium-suggestion 為 clerk 可讀（回 200），唯獨溢價率歷史 MANAGER-only → 403。
    stubFetch((url) => {
      if (url.includes("/settings/premium-rate/history")) return json({ detail: "權限不足" }, 403);
      if (url.includes("/settings")) return json(SETTINGS);
      if (url.includes("/premium-suggestion/today")) return json(SUGGESTION);
      return null;
    });
    renderPage();
    expect(await screen.findByText("需管理者權限")).toBeDefined();
    // clerk 雖能讀 /settings，但無 manager 權限 → 不得出現設定表單
    expect(screen.queryByText("一般設定")).toBeNull();
  });

  it("過時 CLERK token 但後端授權（升權）→ 渲染設定，不被擋", async () => {
    loginAs("CLERK");
    defaultStub();
    renderPage();
    // gate 以後端授權為準：token=CLERK 但 manager-only 歷史端點回 200 → 應看得到一般設定。
    expect(await screen.findByText("一般設定")).toBeDefined();
    expect(screen.queryByText("需管理者權限")).toBeNull();
  });

  it("建議值載入失敗（500）顯示錯誤、不誤呈現為「無建議/資料不足」", async () => {
    loginAs("MANAGER");
    stubFetch((url) => {
      if (url.includes("/settings/premium-rate/history")) return json(HISTORY);
      if (url.includes("/premium-suggestion/today")) return json({ detail: "boom" }, 500);
      if (url.includes("/settings")) return json(SETTINGS);
      return null;
    });
    renderPage();
    expect(await screen.findByText(/讀取當日建議值失敗/)).toBeDefined();
    expect(screen.queryByText("資料不足，採用預設值")).toBeNull();
  });

  it("變更紀錄載入失敗（500）顯示錯誤、不誤呈現為空白稽核紀錄", async () => {
    loginAs("MANAGER");
    stubFetch((url) => {
      if (url.includes("/settings/premium-rate/history")) return json({ detail: "boom" }, 500);
      if (url.includes("/premium-suggestion/today")) return json(SUGGESTION);
      if (url.includes("/settings")) return json(SETTINGS);
      return null;
    });
    renderPage();
    expect(await screen.findByText("讀取變更紀錄失敗，請稍後再試")).toBeDefined();
    expect(screen.queryByText("尚無變更紀錄")).toBeNull();
  });

  it("tax_rate 非標準字串（0.05）不被誤判為變更、不送空操作 PATCH", async () => {
    loginAs("MANAGER");
    const bodies: string[] = [];
    stubFetch((url, init) => {
      if (url.includes("/settings/premium-rate/history")) return json(HISTORY);
      if (url.includes("/premium-suggestion/today")) return json(SUGGESTION);
      if (url.includes("/settings") && init?.method === "PATCH") {
        bodies.push(String(init.body));
        return json(SETTINGS);
      }
      if (url.includes("/settings")) return json({ ...SETTINGS, tax_rate: "0.05" });
      return null;
    });
    renderPage();
    // 只改抽成觸發 PATCH；稅率未動，body 不應含 tax_rate（0.0500 vs 0.05 數值相同）。
    const commissionInput = await screen.findByLabelText("寄售抽成預設 (%)");
    await userEvent.clear(commissionInput);
    await userEvent.type(commissionInput, "60");
    await userEvent.click(screen.getByRole("button", { name: "儲存一般設定" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    const parsed = JSON.parse(bodies[0]) as Record<string, unknown>;
    expect(parsed.default_commission_pct).toBe(60);
    expect(parsed).not.toHaveProperty("tax_rate");
  });

  it("寄售抽成/毛利非整數輸入（50.5）被擋、不靜默存錯值", async () => {
    loginAs("MANAGER");
    const bodies: string[] = [];
    stubFetch((url, init) => {
      if (url.includes("/settings/premium-rate/history")) return json(HISTORY);
      if (url.includes("/premium-suggestion/today")) return json(SUGGESTION);
      if (url.includes("/settings") && init?.method === "PATCH") {
        bodies.push(String(init.body));
        return json(SETTINGS);
      }
      if (url.includes("/settings")) return json(SETTINGS);
      return null;
    });
    renderPage();
    const commissionInput = await screen.findByLabelText("寄售抽成預設 (%)");
    await userEvent.clear(commissionInput);
    await userEvent.type(commissionInput, "50.5");
    await userEvent.click(screen.getByRole("button", { name: "儲存一般設定" }));
    expect(await screen.findByText("寄售抽成請輸入 0-100 的整數")).toBeDefined();
    expect(bodies).toHaveLength(0); // 未送出（不會把 50.5 截成 50）
  });

  it("MANAGER 渲染一般設定區、顯示目前值", async () => {
    loginAs("MANAGER");
    defaultStub();
    renderPage();
    expect(await screen.findByText("一般設定")).toBeDefined();
    // 寄售抽成顯示 50
    const commissionInput = screen.getByLabelText("寄售抽成預設 (%)");
    expect((commissionInput as HTMLInputElement).value).toBe("50");
    // 定價目標毛利顯示 45
    const marginInput = screen.getByLabelText("定價目標毛利 (%)");
    expect((marginInput as HTMLInputElement).value).toBe("45");
  });

  it("MANAGER 看到溢價率區且顯示當日建議值", async () => {
    loginAs("MANAGER");
    defaultStub();
    renderPage();
    expect(await screen.findByText("溢價率設定")).toBeDefined();
    // 目前溢價率 10% (may appear in history too)
    expect(screen.getAllByText("10%").length).toBeGreaterThanOrEqual(1);
    // 建議值 12.5%
    expect(screen.getAllByText("12.5%").length).toBeGreaterThanOrEqual(1);
    // 約束摘要
    expect(screen.getByText(/毛利約束/)).toBeDefined();
    expect(screen.getByText(/負債約束/)).toBeDefined();
  });

  it("一鍵採納建議值：將建議值填入輸入欄", async () => {
    loginAs("MANAGER");
    defaultStub();
    renderPage();
    await screen.findByText("溢價率設定");
    const adoptBtn = screen.getByRole("button", { name: "採納建議值" });
    await userEvent.click(adoptBtn);
    const rateInput = screen.getByLabelText("溢價率 (%)") as HTMLInputElement;
    expect(rateInput.value).toBe("12.5");
  });

  it("insufficient_data 時顯示資料不足提示", async () => {
    loginAs("MANAGER");
    defaultStub({
      suggestion: { ...SUGGESTION, insufficient_data: true, suggested_rate: "0.1000" },
    });
    renderPage();
    expect(await screen.findByText(/資料不足，採用預設值/)).toBeDefined();
  });

  it("儲存一般設定觸發 PATCH 且僅送變更欄位", async () => {
    loginAs("MANAGER");
    const bodies: string[] = [];
    stubFetch((url, init) => {
      if (url.includes("/settings/premium-rate/history")) return json(HISTORY);
      if (url.includes("/premium-suggestion/today")) return json(SUGGESTION);
      if (url.includes("/settings") && init?.method === "PATCH") {
        bodies.push(String(init.body));
        return json({ ...SETTINGS, default_commission_pct: 40 });
      }
      if (url.includes("/settings")) return json(SETTINGS);
      return null;
    });
    renderPage();
    const commissionInput = await screen.findByLabelText("寄售抽成預設 (%)");
    await userEvent.clear(commissionInput);
    await userEvent.type(commissionInput, "40");
    const saveBtn = screen.getByRole("button", { name: "儲存一般設定" });
    await userEvent.click(saveBtn);
    await waitFor(() => expect(bodies).toHaveLength(1));
    const parsed = JSON.parse(bodies[0]) as Record<string, unknown>;
    expect(parsed.default_commission_pct).toBe(40);
    // 未變更的欄位不應被送出
    expect(parsed).not.toHaveProperty("premium_rate");
  });

  it("寄售抽成允許 100（後端契約 le=100，不被前端誤擋）", async () => {
    loginAs("MANAGER");
    const bodies: string[] = [];
    stubFetch((url, init) => {
      if (url.includes("/settings/premium-rate/history")) return json(HISTORY);
      if (url.includes("/premium-suggestion/today")) return json(SUGGESTION);
      if (url.includes("/settings") && init?.method === "PATCH") {
        bodies.push(String(init.body));
        return json({ ...SETTINGS, default_commission_pct: 100 });
      }
      if (url.includes("/settings")) return json(SETTINGS);
      return null;
    });
    renderPage();
    const commissionInput = await screen.findByLabelText("寄售抽成預設 (%)");
    await userEvent.clear(commissionInput);
    await userEvent.type(commissionInput, "100");
    await userEvent.click(screen.getByRole("button", { name: "儲存一般設定" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect((JSON.parse(bodies[0]) as Record<string, unknown>).default_commission_pct).toBe(100);
    expect(screen.queryByText(/寄售抽成請輸入/)).toBeNull();
  });

  it("溢價率變更需二次確認後送 PATCH", async () => {
    loginAs("MANAGER");
    const bodies: string[] = [];
    stubFetch((url, init) => {
      if (url.includes("/settings/premium-rate/history")) return json(HISTORY);
      if (url.includes("/premium-suggestion/today")) return json(SUGGESTION);
      if (url.includes("/settings") && init?.method === "PATCH") {
        bodies.push(String(init.body));
        return json({ ...SETTINGS, premium_rate: "0.1250" });
      }
      if (url.includes("/settings")) return json(SETTINGS);
      return null;
    });
    renderPage();
    await screen.findByText("溢價率設定");
    // Adopt suggestion
    await userEvent.click(screen.getByRole("button", { name: "採納建議值" }));
    // Click save premium
    await userEvent.click(screen.getByRole("button", { name: "儲存溢價率" }));
    // Confirmation dialog should appear
    expect(await screen.findByText(/確認變更溢價率/)).toBeDefined();
    // Fill reason
    const reasonInput = screen.getByLabelText("變更原因（選填）");
    await userEvent.type(reasonInput, "跟隨建議值");
    // Confirm
    await userEvent.click(screen.getByRole("button", { name: "確認" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    const parsed = JSON.parse(bodies[0]) as Record<string, unknown>;
    expect(parsed.premium_rate).toBe("0.1250");
    expect(parsed.premium_change_reason).toBe("跟隨建議值");
  });

  it("溢價率變更歷史列表", async () => {
    loginAs("MANAGER");
    defaultStub();
    renderPage();
    expect(await screen.findByText("溢價率變更紀錄")).toBeDefined();
    expect(screen.getByText("8%")).toBeDefined(); // old
    expect(screen.getByText(/調高溢價率/)).toBeDefined(); // reason
  });
});
