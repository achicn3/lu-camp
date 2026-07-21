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
import { clearPendingCatalogCreate } from "@/lib/idempotency";
import { clearToken, setToken } from "@/lib/token";

// 「詳細」鈕含敏感成本，限管理者；測詳細彈窗前需以 MANAGER token 登入。
function loginManager() {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
  setToken(`${b64({ alg: "HS256" })}.${b64({ sub: "1", role: "MANAGER", store_id: 1 })}.sig`);
}

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

const DETAIL = {
  id: 1,
  item_code: "SER-001",
  name: "登山帳篷",
  brand_id: null,
  category_id: null,
  grade: "A",
  ownership_type: "CONSIGNMENT",
  status: "IN_STOCK",
  commission_pct: 50,
  listed_price: "3500",
  acquisition_cost: null,
  intake_date: "2026-06-01T00:00:00Z",
  sold_date: null,
  sold_price: null,
  margin: null,
  source: { contact_id: 7, name: "寄售人甲", phone: "0911222333", kind: "CONSIGNOR" },
  acquisition_id: null,
  acquisition_type: null,
  sale_id: null,
  history: [{ at: "2026-06-01T00:00:00Z", event: "入庫（收購）", qty: 1, note: "acquisition#1" }],
};

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const CATALOG_DETAIL = {
  id: 2,
  sku: "SKU-9",
  name: "瓦斯罐",
  brand_id: null,
  unit_price: "120",
  quantity_on_hand: 2,
  reorder_point: 5,
  purchases: [
    {
      po_id: 7,
      supplier_id: 5,
      supplier_name: "山野貿易",
      qty: 10,
      unit_cost: "60",
      status: "RECEIVED",
      ordered_at: "2026-06-20T00:00:00Z",
      received_at: "2026-06-21T00:00:00Z",
    },
  ],
  history: [{ at: "2026-06-21T00:00:00Z", event: "入庫（進貨）", qty: 10, note: "purchase_order#7" }],
};

const BULK_DETAIL = {
  id: 3,
  lot_code: "LOT-7",
  name: "雜物堆",
  brand_id: null,
  category_id: null,
  grade: "E",
  acquisition_cost: "300",
  unit_price: "50",
  total_qty: 10,
  remaining_qty: 4,
  intake_date: "2026-06-01T00:00:00Z",
  source: { contact_id: 9, name: "散裝寄售人", phone: "0922333444", kind: "CONSIGNOR" },
  acquisition_id: null,
  acquisition_type: null,
  history: [{ at: "2026-06-01T00:00:00Z", event: "入庫（收購）", qty: 10, note: null }],
};

function route(url: string): Response | null {
  if (url.includes("/catalog-products/") && url.includes("/detail")) return json(CATALOG_DETAIL);
  if (url.includes("/bulk-lots/") && url.includes("/detail")) return json(BULK_DETAIL);
  if (url.includes("/serialized-items/") && url.includes("/detail")) return json(DETAIL);
  if (url.includes("/serialized-items")) return json(SERIALIZED);
  if (url.includes("/catalog-products")) return json(CATALOG);
  if (url.includes("/bulk-lots")) return json(BULK);
  if (url.includes("/brands")) return json([]);
  if (url.includes("/categories")) return json([]);
  return null;
}

function stubInventory() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      const resp = route(url);
      if (resp) return resp;
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
  clearPendingCatalogCreate(1);
  clearToken();
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
    await userEvent.click(screen.getByRole("tab", { name: "一般商品" }));
    expect(await screen.findByText("SKU-9")).toBeTruthy();
    expect(screen.getByText("低庫存")).toBeTruthy(); // 2 <= 5
  });

  it("一般商品上架回應不明後，切換分頁會還原原請求與冪等鍵", async () => {
    loginManager();
    const calls: { key: string | null; body: unknown }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
        if (url.includes("/catalog-products") && method === "POST") {
          const request = input instanceof Request ? input : new Request(url, init);
          calls.push({
            key: request.headers.get("Idempotency-Key"),
            body: JSON.parse(await request.clone().text()),
          });
          return calls.length === 1
            ? json({ detail: "暫時無法確認建立結果" }, 500)
            : json(
                {
                  ...CATALOG[0],
                  id: 90,
                  sku: "AUTO-C1D2E3F4A5B6",
                  name: "庫存頁營繩",
                  unit_price: "280",
                  quantity_on_hand: 0,
                  reorder_point: 4,
                },
                201,
              );
        }
        const resp = route(url);
        if (resp) return resp;
        throw new Error(`unmatched fetch: ${url}`);
      }),
    );
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("tab", { name: "一般商品" }));
    await user.click(await screen.findByText("＋ 上架一般商品"));
    await user.type(screen.getByLabelText("品名"), "庫存頁營繩");
    await user.type(screen.getByLabelText("售價"), "280");
    await user.clear(screen.getByLabelText("低庫存提醒點"));
    await user.type(screen.getByLabelText("低庫存提醒點"), "4");
    await user.click(screen.getByRole("button", { name: "上架商品" }));
    expect(await screen.findByText("暫時無法確認建立結果")).toBeTruthy();

    await user.click(screen.getByRole("tab", { name: "序號品" }));
    await user.click(screen.getByRole("tab", { name: "一般商品" }));
    await user.click(await screen.findByText("＋ 上架一般商品"));
    expect((screen.getByLabelText("品名") as HTMLInputElement).value).toBe("庫存頁營繩");
    expect((screen.getByLabelText("售價") as HTMLInputElement).value).toBe("280");
    expect((screen.getByLabelText("低庫存提醒點") as HTMLInputElement).value).toBe("4");
    await user.click(screen.getByRole("button", { name: "重試並確認上架結果" }));

    expect(await screen.findByText(/SKU AUTO-C1D2E3F4A5B6/)).toBeTruthy();
    expect(calls).toHaveLength(2);
    expect(calls[1]).toEqual(calls[0]);
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
        const resp = route(url);
        if (resp) return resp;
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
        if (url.includes("/serialized-items") && !url.includes("/detail"))
          return json([{ ...SERIALIZED[0], status: "SOLD", sold_date: "2026-06-10T00:00:00Z" }]);
        const resp = route(url);
        if (resp) return resp;
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

  it("詳細 opens a modal showing source and history", async () => {
    stubInventory();
    loginManager();
    renderPage();
    await screen.findByText("SER-001");
    await userEvent.click(screen.getByRole("button", { name: "詳細" }));
    expect(await screen.findByText("商品明細")).toBeTruthy();
    expect(screen.getByText(/寄售人甲/)).toBeTruthy();
    expect(screen.getByText("入庫（收購）")).toBeTruthy();
  });

  it("一般商品 詳細 modal shows supplier purchase history", async () => {
    stubInventory();
    loginManager();
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "一般商品" }));
    await screen.findByText("SKU-9");
    await userEvent.click(screen.getByRole("button", { name: "詳細" }));
    expect(await screen.findByText("一般商品明細")).toBeTruthy();
    expect(screen.getByText("山野貿易")).toBeTruthy();
    expect(screen.getByText("經銷商進貨歷史")).toBeTruthy();
  });

  it("散裝批 詳細 modal shows source and history", async () => {
    stubInventory();
    loginManager();
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "散裝批" }));
    await screen.findByText("LOT-7");
    await userEvent.click(screen.getByRole("button", { name: "詳細" }));
    expect(await screen.findByText("散裝批明細")).toBeTruthy();
    expect(screen.getByText(/散裝寄售人/)).toBeTruthy();
  });

  it("管理者可改序號品售價（PATCH /price，含稅整數元）", async () => {
    loginManager();
    let patched: { url: string; body: unknown } | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
        if (url.includes("/serialized-items/") && url.includes("/price") && method === "PATCH") {
          const raw =
            input instanceof Request ? await input.clone().text() : String(init?.body ?? "{}");
          patched = { url, body: JSON.parse(raw) };
          return json({ ...SERIALIZED[0], listed_price: "4200" });
        }
        const resp = route(url);
        if (resp) return resp;
        throw new Error(`unmatched fetch: ${url}`);
      }),
    );
    renderPage();
    await screen.findByText("SER-001");
    await userEvent.click(screen.getByRole("button", { name: "改價" }));
    const input = await screen.findByLabelText("新售價");
    await userEvent.clear(input);
    await userEvent.type(input, "4200");
    await userEvent.click(screen.getByRole("button", { name: "送出" }));
    await waitFor(() => expect(patched).not.toBeNull());
    expect(patched!.url).toContain("/serialized-items/1/price");
    expect(patched!.body).toEqual({ unit_price: "4200" });
  });

  it("久滯庫存 tab queries by min_age_days and shows days-in-stock", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        urls.push(url);
        const resp = route(url);
        if (resp) return resp;
        throw new Error(`unmatched fetch: ${url}`);
      }),
    );
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "久滯庫存" }));
    await waitFor(() =>
      expect(urls.some((u) => u.includes("min_age_days=90") && u.includes("oldest_first=true"))).toBe(
        true,
      ),
    );
    expect(await screen.findByText("SER-001")).toBeTruthy();
  });
});
