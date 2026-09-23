// @vitest-environment jsdom
// 庫存頁「販售籃」（ADR-025）：一籃一列看總剩餘，展開看每次收購的來源；
// 管理者改籃價後提醒補印籃子標籤。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BasketPanel } from "@/features/inventory/BasketPanel";
import { setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

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
  remaining_qty: 18,
  sources: [
    {
      bulk_lot_id: 1,
      lot_code: "L1-AAAAAAAAAA",
      intake_date: "2026-09-20T02:00:00Z",
      total_qty: 10,
      remaining_qty: 0,
      status: "SOLD_OUT",
      acquisition_cost: "50",
      unit_cost: "5",
    },
    {
      bulk_lot_id: 2,
      lot_code: "L1-BBBBBBBBBB",
      intake_date: "2026-09-22T02:00:00Z",
      total_qty: 20,
      remaining_qty: 18,
      status: "ON_SALE",
      acquisition_cost: "160",
      unit_cost: "8",
    },
  ],
  cost_reference: { sample_count: 2, unit_cost_min: "5", unit_cost_max: "8" },
};

let patched: unknown[] = [];

function stub() {
  patched = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      const body = input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
      if (url.includes("/bulk-baskets/5") && method === "PATCH") {
        patched.push(JSON.parse(body));
        return json({ ...BASKET, unit_price: "15" });
      }
      if (url.includes("/bulk-baskets")) return json([BASKET]);
      if (url.includes("/brands")) return json([]);
      return json([]);
    }),
  );
}

function renderPanel(role: "MANAGER" | "CLERK") {
  setToken(fakeJwt({ sub: "1", role, store_id: 1 }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<BasketPanel />, { wrapper });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("庫存：販售籃", () => {
  it("一籃一列：總剩餘、每件售價、單件收購成本區間", async () => {
    stub();
    renderPanel("CLERK");
    const row = (await screen.findByText("無品牌營釘")).closest("tr") as HTMLElement;
    expect(within(row).getByText("18")).toBeTruthy();
    expect(within(row).getByText("20")).toBeTruthy();
    expect(within(row).getByText("5–8")).toBeTruthy();
  });

  it("展開看每次收購的來源：數量與成本各自保留", async () => {
    stub();
    renderPanel("CLERK");
    await userEvent.click(await screen.findByRole("button", { name: /來源（2 批）/ }));
    expect(screen.getByText("L1-AAAAAAAAAA")).toBeTruthy();
    expect(screen.getByText("L1-BBBBBBBBBB")).toBeTruthy();
  });

  it("店員看不到改價", async () => {
    stub();
    renderPanel("CLERK");
    await screen.findByText("無品牌營釘");
    expect(screen.queryByRole("button", { name: "改售價" })).toBeNull();
  });

  it("管理者改籃價：送出新價並提醒補印籃子標籤", async () => {
    stub();
    renderPanel("MANAGER");
    await userEvent.click(await screen.findByRole("button", { name: "改售價" }));
    const input = screen.getByLabelText("無品牌營釘 新售價");
    await userEvent.clear(input);
    await userEvent.type(input, "15");
    await userEvent.click(screen.getByRole("button", { name: "儲存售價" }));
    await waitFor(() => expect(patched).toEqual([{ unit_price: "15" }]));
    expect(await screen.findByText(/請補印籃子標籤/)).toBeTruthy();
  });
});
