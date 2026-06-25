// @vitest-environment jsdom
// /purchasing 採購工作台：採購單清單 + 收貨、建單、供應商建檔、低庫存提醒。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import PurchasingPage from "@/app/(authed)/purchasing/page";
import { clearToken, setToken } from "@/lib/token";

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

const SUPPLIER = {
  id: 5,
  store_id: 1,
  name: "山林供應商",
  contact: "0911-222-333",
  tax_id: "12345678",
  created_at: "2026-06-20T00:00:00Z",
  updated_at: "2026-06-20T00:00:00Z",
};

const CATALOG = {
  id: 42,
  store_id: 1,
  sku: "GAS-001",
  name: "瓦斯罐",
  brand_id: null,
  unit_price: "120",
  quantity_on_hand: 1,
  reorder_point: 5,
};

const ORDERED_PO = {
  id: 7,
  store_id: 1,
  supplier_id: 5,
  status: "ORDERED",
  ordered_by: 1,
  ordered_at: "2026-06-20T01:00:00Z",
  received_at: null,
  received_by: null,
  created_at: "2026-06-20T01:00:00Z",
  updated_at: "2026-06-20T01:00:00Z",
  total_cost: "600",
  lines: [{ id: 1, catalog_product_id: 42, qty: 10, unit_cost: "60", line_total: "600" }],
};

type FetchRoute = (url: string, init: RequestInit) => Response | null;

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

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<PurchasingPage />, { wrapper });
}

afterEach(() => {
  cleanup();
  clearToken();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("/purchasing", () => {
  it("shows low-stock reminders and existing purchase orders with a receive action", async () => {
    loginAs("CLERK");
    stubFetch((url) => {
      if (url.includes("/suppliers")) return json([SUPPLIER]);
      if (url.includes("/catalog-products") && url.includes("low_stock=true")) return json([CATALOG]);
      if (url.includes("/purchase-orders")) return json([ORDERED_PO]);
      return null;
    });
    renderPage();

    expect(await screen.findByText("瓦斯罐")).toBeTruthy();
    expect(screen.getByText(/現量 1 \/ 補貨點 5/)).toBeTruthy();
    // 供應商欄改為「查無即建」combobox（與收購頁品牌一致），採購單清單仍以文字顯示供應商名。
    expect(screen.getByLabelText("供應商")).toBeTruthy();
    expect(await screen.findByText("山林供應商")).toBeTruthy();
    expect(screen.getByRole("button", { name: "收貨入庫" })).toBeTruthy();
    expect(screen.getByText("已下單")).toBeTruthy();
  });

  it("receives a purchase order after confirmation", async () => {
    loginAs("CLERK");
    const received = { ...ORDERED_PO, status: "RECEIVED", received_at: "2026-06-20T02:00:00Z" };
    let receivePosted = false;
    stubFetch((url, init) => {
      if (url.includes("/suppliers")) return json([SUPPLIER]);
      if (url.includes("/catalog-products")) return json([]);
      if (url.includes("/receive") && init.method === "POST") {
        receivePosted = true;
        return json({ receipt_id: 1, purchase_order: received });
      }
      if (url.includes("/purchase-orders")) return json(receivePosted ? [received] : [ORDERED_PO]);
      return null;
    });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "收貨入庫" }));
    await user.click(await screen.findByRole("button", { name: "確認收貨" }));

    await waitFor(() => expect(receivePosted).toBe(true));
    // 狀態篩選 chip 也有「已收貨」字樣，故鎖定採購單列的狀態徽章（span.inv-badge）。
    expect(await screen.findByText("已收貨", { selector: "span.inv-badge" })).toBeTruthy();
  });

  it("builds a purchase order from a searched catalog product", async () => {
    loginAs("CLERK");
    let createdBody: string | null = null;
    stubFetch((url, init) => {
      if (url.includes("/suppliers")) return json([SUPPLIER]);
      if (url.includes("/catalog-products") && url.includes("low_stock=true")) return json([]);
      if (url.includes("/catalog-products")) return json([CATALOG]);
      if (url.includes("/purchase-orders") && init.method === "POST") {
        createdBody = init.body as string;
        return json(ORDERED_PO, 201);
      }
      if (url.includes("/purchase-orders")) return json([]);
      return null;
    });
    const user = userEvent.setup();
    renderPage();

    const supplierInput = screen.getByLabelText("供應商");
    await user.click(supplierInput);
    await user.type(supplierInput, "山林");
    await user.click(await screen.findByRole("option", { name: "山林供應商" }));
    await user.type(screen.getByLabelText("搜尋數量品"), "瓦斯");
    await user.click(await screen.findByRole("button", { name: /瓦斯罐/ }));
    await user.type(screen.getByLabelText("進貨單價 瓦斯罐"), "60");
    await user.click(screen.getByRole("button", { name: "建立採購單" }));

    await waitFor(() => expect(createdBody).not.toBeNull());
    const parsed = JSON.parse(createdBody as unknown as string);
    expect(parsed.supplier_id).toBe(5);
    expect(parsed.lines).toEqual([{ catalog_product_id: 42, qty: 1, unit_cost: "60" }]);
  });

  it("creates a supplier from the suppliers tab", async () => {
    loginAs("MANAGER");
    let createdBody: string | null = null;
    stubFetch((url, init) => {
      if (url.includes("/suppliers") && init.method === "POST") {
        createdBody = init.body as string;
        return json(SUPPLIER, 201);
      }
      if (url.includes("/suppliers")) return json([]);
      if (url.includes("/catalog-products")) return json([]);
      if (url.includes("/purchase-orders")) return json([]);
      return null;
    });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "供應商" }));
    await user.type(await screen.findByLabelText("供應商名稱"), "新供應商");
    await user.type(screen.getByLabelText("統一編號"), "87654321");
    await user.click(screen.getByRole("button", { name: "新增供應商" }));

    await waitFor(() => expect(createdBody).not.toBeNull());
    const parsed = JSON.parse(createdBody as unknown as string);
    expect(parsed.name).toBe("新供應商");
    expect(parsed.tax_id).toBe("87654321");
  });

  it("採購單狀態篩選會帶上 status 查詢參數", async () => {
    loginAs("CLERK");
    const poUrls: string[] = [];
    stubFetch((url) => {
      if (url.includes("/suppliers")) return json([SUPPLIER]);
      if (url.includes("/catalog-products")) return json([]);
      if (url.includes("/purchase-orders")) {
        poUrls.push(url);
        return json([ORDERED_PO]);
      }
      return null;
    });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("已下單"); // 初次載入（全部，無 status 參數）
    expect(poUrls.some((u) => !u.includes("status="))).toBe(true);

    await user.click(screen.getByRole("button", { name: "待收貨" }));
    await waitFor(() => expect(poUrls.some((u) => u.includes("status=ORDERED"))).toBe(true));
  });
});
