// @vitest-environment jsdom
// /inventory 庫存頁測試：三分頁清單渲染、狀態/持有 badge、低庫存標示、售出進度、分頁切換。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import InventoryPage from "@/app/(authed)/inventory/page";

const SERIALIZED = [
  {
    id: 1,
    store_id: 1,
    item_code: "SER-001",
    name: "登山帳篷",
    grade: "A",
    ownership_type: "CONSIGNMENT",
    status: "IN_STOCK",
    listed_price: "3500",
    brand_id: null,
    product_model_id: null,
    commission_pct: 50,
    consignor_id: 7,
    intake_date: "2026-06-01T00:00:00Z",
    sold_date: null,
  },
];
const CATALOG = [
  {
    id: 2,
    store_id: 1,
    sku: "SKU-9",
    name: "瓦斯罐",
    unit_price: "120",
    quantity_on_hand: 2,
    reorder_point: 5,
    brand_id: null,
  },
];
const BULK = [
  {
    id: 3,
    store_id: 1,
    lot_code: "LOT-7",
    name: "雜物堆",
    label: null,
    grade: "E",
    acquisition_cost: "300",
    acquisition_basis: "BAG",
    unit_price: "50",
    total_qty: 10,
    remaining_qty: 4,
    status: "ON_SALE",
    brand_id: null,
  },
];

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubInventory() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.includes("/serialized-items")) return json(SERIALIZED);
      if (url.includes("/catalog-products")) return json(CATALOG);
      if (url.includes("/bulk-lots")) return json(BULK);
      throw new Error(`unmatched fetch: ${url}`);
    }),
  );
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<InventoryPage />, { wrapper });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("InventoryPage", () => {
  it("serialized tab lists items with ownership + status badges", async () => {
    stubInventory();
    renderPage();
    expect(await screen.findByText("SER-001")).toBeTruthy();
    expect(screen.getByText("登山帳篷")).toBeTruthy();
    // 狀態文字也出現在篩選下拉，故鎖定 badge span（避免多重匹配）。
    expect(screen.getByText("寄售", { selector: ".inv-badge" })).toBeTruthy();
    expect(screen.getByText("在庫", { selector: ".inv-badge" })).toBeTruthy();
  });

  it("catalog tab flags low stock (qty<=reorder_point)", async () => {
    stubInventory();
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "數量品" }));
    expect(await screen.findByText("SKU-9")).toBeTruthy();
    expect(screen.getByText("低庫存")).toBeTruthy(); // 2 <= 5
  });

  it("bulk tab shows sell-through percent", async () => {
    stubInventory();
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "散裝批" }));
    expect(await screen.findByText("LOT-7")).toBeTruthy();
    expect(screen.getByText("60%")).toBeTruthy(); // (10-4)/10
    expect(screen.getByText("販售中", { selector: ".inv-badge" })).toBeTruthy();
  });

  it("serialized row reprints a label via the hardware agent", async () => {
    const calls: { url: string; body: unknown }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("/print/label")) {
          calls.push({ url, body: JSON.parse(String(init?.body ?? "{}")) });
          return json({ ok: true });
        }
        if (url.includes("/serialized-items")) return json(SERIALIZED);
        if (url.includes("/catalog-products")) return json(CATALOG);
        if (url.includes("/bulk-lots")) return json(BULK);
        throw new Error(`unmatched fetch: ${url}`);
      }),
    );
    renderPage();
    await screen.findByText("SER-001");
    await userEvent.click(screen.getByRole("button", { name: "補印標籤" }));
    expect(await screen.findByText("✓ 已送出")).toBeTruthy();
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toContain("/print/label");
    expect(calls[0].body).toEqual({ code: "SER-001", name: "登山帳篷", price: 3500 });
  });

  it("sold serialized item shows no reprint button", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("/serialized-items"))
          return json([{ ...SERIALIZED[0], status: "SOLD", sold_date: "2026-06-10T00:00:00Z" }]);
        if (url.includes("/catalog-products")) return json(CATALOG);
        if (url.includes("/bulk-lots")) return json(BULK);
        throw new Error(`unmatched fetch: ${url}`);
      }),
    );
    renderPage();
    await screen.findByText("SER-001");
    expect(screen.queryByRole("button", { name: "補印標籤" })).toBeNull();
  });

  it("paginates: next disabled when page not full", async () => {
    stubInventory();
    renderPage();
    await screen.findByText("SER-001");
    const next = screen.getByRole("button", { name: "下一頁" });
    await waitFor(() => expect(next).toHaveProperty("disabled", true)); // 1 row < PAGE_SIZE
  });
});
