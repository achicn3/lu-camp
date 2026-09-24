// @vitest-environment jsdom
// 活動成效（docs/40 P1d）：每個活動只算真的套到它的商品；列出可疊加、指定範圍、這筆不套用次數。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import ReportsPage from "@/app/(authed)/reports/page";
import { clearToken, setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

const ROW = {
  campaign_id: 7,
  name: "Snow Peak 八折",
  status: "ACTIVE",
  discount_pct: 20,
  starts_at: "2026-09-01T00:00:00Z",
  ends_at: "2026-09-30T00:00:00Z",
  campaign_discount_total: "400",
  gross_turnover: "1600",
  recognized_revenue: "1600",
  gross_margin: "1000",
  gross_margin_rate: "0.625",
  transaction_count: 2,
  stackable: true,
  targets: [{ mode: "INCLUDE", target_type: "BRAND", target_id: 5, label: "Snow Peak" }],
  not_applied_count: 3,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearToken();
});

describe("活動成效", () => {
  it("列出可疊加、指定範圍、這筆不套用次數，並說明只算套到該活動的商品", async () => {
    setToken(fakeJwt({ sub: "1", role: "MANAGER", store_id: 1 }));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("/reports/campaign-performance")) {
          return new Response(
            JSON.stringify({ generated_at: "2026-09-24T00:00:00Z", store_id: 1, rows: [ROW] }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        // 報表頁先以購物金餘額探測權限；回 200 才會顯示報表分頁。
        if (url.includes("/store-credit/liability")) {
          return new Response(
            JSON.stringify({
              generated_at: "2026-09-24T00:00:00Z",
              store_id: 1,
              total_outstanding: "0",
              aging_buckets: { lt_30d: "0", d30_90: "0", d90_180: "0", d180_365: "0", gt_365d: "0" },
              per_member: [],
              liability_health_ratio: null,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response("{}", { status: 500 });
      }),
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    render(<ReportsPage />, { wrapper });
    const user = userEvent.setup();
    await user.click(
      within(await screen.findByRole("tablist", { name: "報表分類" })).getByRole("tab", {
        name: "促銷",
      }),
    );
    await user.click(
      within(screen.getByRole("tablist", { name: "報表" })).getByRole("tab", { name: "活動成效" }),
    );

    const row = (await screen.findByText("Snow Peak 八折")).closest("tr") as HTMLElement;
    expect(row.textContent).toContain("可疊加");
    expect(row.textContent).toContain("只限：Snow Peak");
    expect(within(row).getByText("3")).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "這筆不套用" })).toBeTruthy();
    expect(screen.getByText(/只算真的套到這個活動的商品/)).toBeTruthy();
  });
});
