// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "1" }),
}));

import MemberDetailPage from "@/app/(authed)/contacts/[id]/page";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("會員資料頁", () => {
  it("返回會員列表使用標準次要按鈕樣式與完整標籤", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    render(<MemberDetailPage />, { wrapper });

    const back = screen.getByRole("link", { name: "返回會員列表" });
    expect(back.getAttribute("href")).toBe("/contacts");
    expect(back.classList.contains("btn-secondary")).toBe(true);
    expect(back.classList.contains("member-back-link")).toBe(true);
  });

  it("總覽以中文顯示寄售待付款狀態", async () => {
    const overview = {
      contact: {
        id: 1,
        store_id: 1,
        name: "測試會員",
        phone: "0912345678",
        address: null,
        roles: ["MEMBER"],
        member_points: 0,
        default_carrier_type: null,
        default_carrier_id: null,
        source_note: null,
        national_id_masked: null,
        has_national_id: false,
      },
      member_points: 0,
      store_credit_balance: "0",
      pending_consignment_payout: "0",
      counts: { purchases: 0, consigned_items: 0 },
      recent_purchases: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify(overview), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(<MemberDetailPage />, {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    });

    expect(await screen.findByText("寄售待撥（待付款）")).toBeTruthy();
    expect(screen.queryByText(/PENDING/)).toBeNull();
  });
});
