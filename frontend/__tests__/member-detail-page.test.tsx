// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "1" }),
}));

import MemberDetailPage from "@/app/(authed)/contacts/[id]/page";

afterEach(() => {
  cleanup();
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

  it("舊資料的電話不合新規則時，只改住址仍存得起來（PATCH 不帶沒動過的電話）", async () => {
    // 2026-09-16 加上手機格式檢查之前，庫裡有市話與不完整號碼。表單每次都送電話的話，
    // 店員只改住址也會被擋，那筆資料就永遠編不了——而他根本沒碰電話欄。
    const contact = {
      id: 1,
      store_id: 1,
      name: "舊會員",
      phone: "956475589", // 不合新規則的既有資料
      address: null,
      roles: ["MEMBER"],
      member_points: 0,
      default_carrier_type: null,
      default_carrier_id: null,
      source_note: null,
      national_id_masked: null,
      has_national_id: false,
    };
    const overview = {
      contact,
      member_points: 0,
      store_credit_balance: "0",
      pending_consignment_payout: "0",
      counts: { purchases: 0, consigned_items: 0 },
      recent_purchases: [],
    };
    const patches: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
        if (method === "PATCH") {
          patches.push(JSON.parse(String(init?.body ?? "{}")));
          return new Response(JSON.stringify({ ...contact, address: "台北市中正區" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify(overview), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // 這個檔沒有測試間自動清理，前面留下的 DOM 會讓 screen.* 抓到多個同名元素。
    const { container } = render(<MemberDetailPage />, {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    });
    const view = within(container);

    await userEvent.click(await view.findByRole("button", { name: "編輯" }));
    const address = (await view.findByLabelText("住址（切結書顯示用）")) as HTMLInputElement;
    await userEvent.type(address, "台北市中正區");
    await userEvent.click(view.getByRole("button", { name: "儲存" }));

    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0]).not.toHaveProperty("phone"); // 沒動過就不要送
    expect(view.queryByText(/09 開頭的 10 碼/)).toBeNull();
  });

  it.each(["0911", ""])("修改電話為 %s 時擋下，不可假裝存成原值", async (value) => {
    const contact = {
      id: 1, store_id: 1, name: "會員", phone: "0912345678", address: null,
      roles: ["MEMBER"], member_points: 0, default_carrier_type: null,
      default_carrier_id: null, source_note: null, national_id_masked: null, has_national_id: false,
    };
    const overview = {
      contact, member_points: 0, store_credit_balance: "0", pending_consignment_payout: "0",
      counts: { purchases: 0, consigned_items: 0 }, recent_purchases: [],
    };
    const patches: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
        if (method === "PATCH") {
          patches.push(1);
          return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
        }
        return new Response(JSON.stringify(overview), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(<MemberDetailPage />, {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    });
    const view = within(container);

    await userEvent.click(await view.findByRole("button", { name: "編輯" }));
    const phone = (await view.findByLabelText("電話")) as HTMLInputElement;
    await userEvent.clear(phone);
    if (value) await userEvent.type(phone, value);
    await userEvent.click(view.getByRole("button", { name: "儲存" }));

    expect(await view.findByText(/09 開頭的 10 碼/)).toBeTruthy();
    expect(patches).toHaveLength(0);
  });
});
