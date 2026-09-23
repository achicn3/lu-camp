// @vitest-environment jsdom
// 收購散裝加入販售籃（ADR-025）：選了籃子，名稱／品牌／分類／每件售價就以籃子為準，
// 店員只填本次件數與整堆收購成本；送出帶 basket_id。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import AcquisitionPage from "@/app/(authed)/acquisition/page";

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const BASKET = {
  id: 5,
  store_id: 1,
  code: "K1-ABCDEF0123",
  name: "無品牌營釘",
  brand_id: null,
  category_id: null,
  unit_price: "20",
  note: null,
  is_active: true,
  remaining_qty: 30,
  sources: [],
  cost_reference: { sample_count: 2, unit_cost_min: "5", unit_cost_max: "8" },
};

function stub() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      if (url.includes("/bulk-baskets") && method === "GET") return json([BASKET]);
      if (url.includes("/settings")) {
        return json({
          premium_rate: "0.1000",
          default_commission_pct: 50,
          default_margin_pct: 45,
          tax_rate: "0.0500",
          linepay_fee_pct: "0.0000",
          taiwanpay_fee_pct: "0.0000",
        });
      }
      if (url.includes("/cash-sessions/current")) return json({ id: 1, status: "OPEN" });
      return json([]);
    }),
  );
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<AcquisitionPage />, { wrapper });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("收購散裝：販售籃", () => {
  it("預設不入籃，維持原本單獨一張標籤", async () => {
    stub();
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "散裝" }));
    expect((screen.getByLabelText("不放入販售籃") as HTMLInputElement).checked).toBe(true);
  });

  it("加入現有販售籃：帶入籃子的名稱與售價並鎖定，顯示剩餘與歷史單件成本", async () => {
    stub();
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "散裝" }));
    await userEvent.click(screen.getByLabelText("加入現有販售籃"));
    await userEvent.selectOptions(await screen.findByLabelText("販售籃"), "5");

    const name = screen.getByLabelText("名稱") as HTMLInputElement;
    const price = screen.getByLabelText("每件均一價") as HTMLInputElement;
    await waitFor(() => expect(name.value).toBe("無品牌營釘"));
    expect(name.readOnly).toBe(true);
    expect(price.value).toBe("20");
    expect(price.readOnly).toBe(true);
    expect(screen.getByText(/目前 30 件/)).toBeTruthy();
    expect(screen.getByText(/單件收購成本 5–8 元/)).toBeTruthy();
  });

  it("改回不入籃時解除鎖定，讓店員自己填", async () => {
    stub();
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "散裝" }));
    await userEvent.click(screen.getByLabelText("加入現有販售籃"));
    await userEvent.selectOptions(await screen.findByLabelText("販售籃"), "5");
    await userEvent.click(screen.getByLabelText("不放入販售籃"));
    expect((screen.getByLabelText("名稱") as HTMLInputElement).readOnly).toBe(false);
  });
});
