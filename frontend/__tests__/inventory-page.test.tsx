// @vitest-environment jsdom
// /inventory 庫存頁測試：三分頁清單渲染、狀態/持有 badge、低庫存標示、售出進度、分頁切換。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
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
    retail_price: "8000",
    brand_id: 11,
    product_model_id: 21,
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
    is_active: true,
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

const FILTER_OPTIONS = {
  brands: [
    { id: 11, name: "蠻牛" },
    { id: 12, name: "別牌" },
  ],
  models: [{ id: 21, brand_id: 11, name: "營釘 20cm" }],
  categories: [{ id: 31, name: "配件", target_margin_pct: 45 }],
  grades: ["A", "C"],
};

// 記下每次要選項時帶的 brand_id，用來驗「選了品牌就收斂」。
const optionRequests: string[] = [];

function route(url: string): Response | null {
  if (url.endsWith("/settings")) {
    return json({ tax_rate: "0.05", linepay_fee_pct: "0.022", taiwanpay_fee_pct: "0.01" });
  }
  if (url.includes("/serialized-items/filter-options")) {
    optionRequests.push(url);
    return json(FILTER_OPTIONS);
  }
  if (url.includes("/serialized-items/count")) return json({ count: SERIALIZED.length });
  if (url.includes("/catalog-products/count")) return json({ count: CATALOG.length });
  if (url.includes("/catalog-products/filter-options")) return json({ brands: FILTER_OPTIONS.brands });
  if (url.includes("/bulk-lots/count")) return json({ count: BULK.length });
  if (url.includes("/bulk-lots/filter-options")) {
    return json({ brands: FILTER_OPTIONS.brands, categories: FILTER_OPTIONS.categories, grades: ["E"] });
  }
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
  it("管理者看到進貨成本與扣稅扣手續費的毛利率", async () => {
    loginManager();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      if (new URL(url).pathname === "/api/v1/catalog-products") {
        return json([{ ...CATALOG[0], unit_price: "1954", unit_cost: "1000" }]);
      }
      return route(url) ?? json(null, 404);
    }));
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "一般商品" }));
    expect(await screen.findByRole("columnheader", { name: "最新進價" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "預估毛利率" })).toBeTruthy();
    // 手算：round(1954 / 1.05)=1861；round(1954*0.022)=43；實得1818。
    // (1818-1000)/1818=44.994...%，沿收購頁顯示整數45%。
    expect(await screen.findByText("45%")).toBeTruthy();
    expect(screen.getByText("1,000")).toBeTruthy();
  });

  it.each([
    ["0.022", "0.01", "0.10", "41%"],
    ["0.01", "0.022", "0.10", "41%"],
  ])("費率%s/%s與稅率%s由 API 決定", async (linepay, taiwanpay, tax, expected) => {
    loginManager();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.endsWith("/settings")) {
        return json({ tax_rate: tax, linepay_fee_pct: linepay, taiwanpay_fee_pct: taiwanpay });
      }
      if (new URL(url).pathname === "/api/v1/catalog-products") {
        return json([{ ...CATALOG[0], unit_price: "1954", unit_cost: "1020" }]);
      }
      return route(url) ?? json(null, 404);
    }));
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "一般商品" }));
    // round(1954/1.10)-round(1954*0.022)=1776-43=1733；713/1733 → 41%。
    expect(await screen.findByText(expected)).toBeTruthy();
  });

  it("管理者在無成本時兩欄皆顯示破折號", async () => {
    loginManager();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      if (new URL(url).pathname === "/api/v1/catalog-products") {
        return json([{ ...CATALOG[0], unit_cost: null }]);
      }
      return route(url) ?? json(null, 404);
    }));
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "一般商品" }));
    const row = (await screen.findByText("SKU-9")).closest("tr")!;
    const cells = within(row).getAllByRole("cell");
    expect(cells[4].textContent).toBe("—");
    expect(cells[5].textContent).toBe("—");
  });

  it("店員看不到成本與毛利率表頭或儲存格", async () => {
    const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url");
    setToken(`${b64({ alg: "HS256" })}.${b64({ sub: "2", role: "CLERK", store_id: 1 })}.sig`);
    stubInventory();
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "一般商品" }));
    const row = (await screen.findByText("SKU-9")).closest("tr")!;
    expect(screen.queryByRole("columnheader", { name: "最新進價" })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "預估毛利率" })).toBeNull();
    expect(within(row).getAllByRole("cell")).toHaveLength(7);
  });

  it.each(["failed", "missing-fee", "null-fee", "blank-tax"])("設定 %s 時保留成本但不顯示誤導的毛利率", async (mode) => {
    loginManager();
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.endsWith("/settings")) {
        if (mode === "failed") return json({ detail: "unavailable" }, 500);
        return json({ tax_rate: mode === "blank-tax" ? "" : "0.05", linepay_fee_pct: mode === "missing-fee" ? undefined : mode === "null-fee" ? null : "0.022", taiwanpay_fee_pct: "0.01" });
      }
      if (new URL(url).pathname === "/api/v1/catalog-products") return json([{ ...CATALOG[0], unit_price: "1954", unit_cost: "1000" }]);
      return route(url) ?? json(null, 404);
    }));
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "一般商品" }));
    const row = (await screen.findByText("SKU-9")).closest("tr")!;
    await screen.findByText(/無法取得完整稅率或手續費設定/);
    expect(within(row).getAllByRole("cell")[4].textContent).toBe("1,000");
    expect(within(row).getAllByRole("cell")[5].textContent).toBe("—");
  });

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

    expect(await screen.findByText(/商品編號 AUTO-C1D2E3F4A5B6/)).toBeTruthy();
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
    // 品牌獨立一行、二手標示（裁示 2026-09-14）；成色不印。
    expect(calls[0].body).toEqual({
      code: "SER-001",
      name: "登山帳篷",
      price: 3500,
      brand: "蠻牛",
      condition: "二手",
    });
  });

  it("成色「全新未拆」的序號品，標籤印「全新」而不是「二手」（2026-09-16）", async () => {
    const calls: { url: string; body: unknown }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("/print/label")) {
          calls.push({ url, body: JSON.parse(String(init?.body ?? "{}")) });
          return json({ ok: true });
        }
        // 只改清單列的成色；其他路由（品牌名、數量）沿用共用假資料。
        if (new URL(url).pathname.endsWith("/serialized-items")) {
          return json(SERIALIZED.map((row) => ({ ...row, grade: "N" })));
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
    expect(calls[0].body).toEqual({
      code: "SER-001",
      name: "登山帳篷",
      price: 3500,
      brand: "蠻牛",
      condition: "全新",
    });
  });

  // 標籤內容（裁示 2026-09-14）：品牌獨立一行、沒品牌就不送、散裝與一般商品都要能印。
  // 一般商品來自採購＝全新；序號品與散裝批來自收購＝二手。成色不印。
  it.each([
    { tab: "一般商品", row: "SKU-9", code: "SKU-9", name: "瓦斯罐", price: 120, condition: "全新" },
    { tab: "散裝批", row: "LOT-7", code: "LOT-7", name: "雜物堆", price: 50, condition: "二手" },
  ])("$tab row prints a label marked $condition", async (c) => {
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
    await userEvent.click(screen.getByRole("tab", { name: c.tab }));
    await screen.findByText(c.row);
    await userEvent.click(screen.getByRole("button", { name: "補印標籤" }));
    expect(await screen.findByText("✓ 已送出")).toBeTruthy();
    // 這兩筆 fixture 都沒有品牌 → brand 送 null，代理端整行不印。
    expect(calls[0].body).toEqual({
      code: c.code,
      name: c.name,
      price: c.price,
      brand: null,
      condition: c.condition,
    });
  });

  it.each([
    { tab: "序號品", endpoint: "serialized-items", rows: SERIALIZED, code: "SER-001", name: "登山帳篷", price: 3500, condition: "二手" },
    { tab: "一般商品", endpoint: "catalog-products", rows: CATALOG, code: "SKU-9", name: "瓦斯罐", price: 120, condition: "全新" },
    { tab: "散裝批", endpoint: "bulk-lots", rows: BULK, code: "LOT-7", name: "雜物堆", price: 50, condition: "二手" },
  ].flatMap((c) => ["pending", "failed", "missing"].map((state) => ({ ...c, state }))))("$tab blocks printing when the assigned brand is $state, then recovers", async (c) => {
    const { state } = c;
    let release!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => { release = resolve; });
    const labels: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.includes("/print/label")) {
        labels.push(JSON.parse(String(init?.body ?? "{}")));
        return json({ ok: true });
      }
      if (url.includes(`/${c.endpoint}/filter-options`)) {
        if (state === "pending") return pending;
        return state === "failed" ? json({ detail: "unavailable" }, 500) : json({ ...FILTER_OPTIONS, brands: [] });
      }
      if (new URL(url).pathname.endsWith(`/${c.endpoint}`)) return json(c.rows.map((row) => ({ ...row, brand_id: 11 })));
      return route(url) ?? json([]);
    }));
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: c.tab }));
    await screen.findByText(c.code);
    const button = screen.getByRole("button", { name: "補印標籤" });
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(true));
    expect(await screen.findByText(/品牌名稱尚未取得/)).toBeTruthy();
    await userEvent.click(button);
    expect(labels).toHaveLength(0);
    if (state === "pending") {
      release(json(FILTER_OPTIONS));
      await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
      await userEvent.click(button);
      await screen.findByText("✓ 已送出");
      expect(labels).toEqual([{ code: c.code, name: c.name, price: c.price, brand: "蠻牛", condition: c.condition }]);
    }
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
    await userEvent.click(screen.getByRole("button", { name: "編輯" }));
    const input = await screen.findByLabelText("售價");
    await userEvent.clear(input);
    await userEvent.type(input, "4200");
    await userEvent.click(screen.getByRole("button", { name: "儲存" }));
    await waitFor(() => expect(patched).not.toBeNull());
    expect(patched!.url).toContain("/serialized-items/1/price");
    expect(patched!.body).toEqual({ unit_price: "4200" });
  });

it("編輯：全新售價（原價）可以改，也可以清空", async () => {
    // 原價是純記錄——只送它的時候不可以順手把品名或售價也改掉。
    loginManager();
    const patches: { url: string; body: unknown }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
        if (method === "PATCH") {
          const raw =
            input instanceof Request ? await input.clone().text() : String(init?.body ?? "{}");
          patches.push({ url, body: JSON.parse(raw) });
          return json({ ...SERIALIZED[0], retail_price: null });
        }
        const resp = route(url);
        if (resp) return resp;
        throw new Error(`unmatched fetch: ${url}`);
      }),
    );
    renderPage();
    await screen.findByText("SER-001");
    await userEvent.click(screen.getByRole("button", { name: "編輯" }));

    // 現值要帶進來，店員才知道原本填了什麼。
    const retail = (await screen.findByLabelText("全新售價（原價）")) as HTMLInputElement;
    expect(retail.value).toBe("8000");

    await userEvent.clear(retail);
    await userEvent.type(retail, "9500");
    await userEvent.click(screen.getByRole("button", { name: "儲存" }));
    await waitFor(() => expect(patches.length).toBe(1));
    expect(patches[0].url).toContain("/serialized-items/1");
    expect(patches[0].url).not.toContain("/price");
    expect(patches[0].body).toEqual({ retail_price: "9500" });

    // 清空要送 null（不是空字串），否則後端分不出「沒送」與「要清掉」。
    patches.length = 0;
    await userEvent.click(screen.getByRole("button", { name: "編輯" }));
    const again = (await screen.findByLabelText("全新售價（原價）")) as HTMLInputElement;
    await userEvent.clear(again);
    await userEvent.click(screen.getByRole("button", { name: "儲存" }));
    await waitFor(() => expect(patches.length).toBe(1));
    expect(patches[0].body).toEqual({ retail_price: null });
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

  it("序號品清單看得到品牌與型號", async () => {
    stubInventory();
    renderPage();
    // 品牌名同時出現在下拉選項裡，所以鎖定那一列而不是整頁找字。
    const row = (await screen.findByText("SER-001")).closest("tr");
    expect(row?.textContent).toContain("蠻牛");
    expect(row?.textContent).toContain("營釘 20cm");
  });

  it("選了品牌之後，選項要跟著那個品牌重新取得", async () => {
    optionRequests.length = 0;
    stubInventory();
    renderPage();
    await screen.findByText("SER-001");
    // 一開始沒選品牌 → 不帶 brand_id，列出全部實際有的
    await waitFor(() => expect(optionRequests.length).toBeGreaterThan(0));
    expect(optionRequests.some((u) => !u.includes("brand_id"))).toBe(true);

    await userEvent.selectOptions(screen.getByLabelText("品牌"), "11");
    await waitFor(() => expect(optionRequests.some((u) => u.includes("brand_id=11"))).toBe(true));
  });

  it("換品牌會清掉型號與成色——舊的選擇在新品牌下可能根本不存在", async () => {
    stubInventory();
    renderPage();
    await screen.findByText("SER-001");

    const models = screen.getByLabelText("型號") as HTMLSelectElement;
    const grades = screen.getByLabelText("成色") as HTMLSelectElement;
    await userEvent.selectOptions(models, "21");
    await userEvent.selectOptions(grades, "A");
    expect(models.value).toBe("21");
    expect(grades.value).toBe("A");

    await userEvent.selectOptions(screen.getByLabelText("品牌"), "12");
    await waitFor(() => expect(models.value).toBe(""));
    expect(grades.value).toBe("");
  });

  it("欄位順序是序號碼、品牌、品名、型號", async () => {
    stubInventory();
    renderPage();
    await screen.findByText("SER-001");
    const headers = screen
      .getAllByRole("columnheader")
      .map((h) => h.textContent)
      .slice(0, 4);
    expect(headers).toEqual(["序號碼", "品牌", "品名", "型號"]);
  });

  it("分頁顯示總頁數與總件數，不再只說「第 N 頁」", async () => {
    // 這支要多頁，所以總數自己給（共用 stub 的總數要跟只有一列的清單一致）。
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("/serialized-items/count")) return json({ count: 137 });
        const resp = route(url);
        if (resp) return resp;
        throw new Error(`unmatched fetch: ${url}`);
      }),
    );
    renderPage();
    await screen.findByText("SER-001");
    // 137 件、每頁 PAGE_SIZE(20) → 共 7 頁
    expect(await screen.findByText(/第 1 \/ 7 頁・共 137 件/)).toBeTruthy();
  });

  it("一般商品與散裝批也看得到品牌欄與總頁數", async () => {
    stubInventory();
    renderPage();
    await screen.findByText("SER-001");

    await userEvent.click(screen.getByRole("tab", { name: "一般商品" }));
    await screen.findByText("SKU-9");
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent).slice(0, 3))
      .toEqual(["商品編號", "品牌", "品名"]);
    expect(screen.getByText(/共 \d+ 件/)).toBeTruthy();

    await userEvent.click(screen.getByRole("tab", { name: "散裝批" }));
    await screen.findByText(/第 \d+ \/ \d+ 頁/);
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent).slice(0, 3))
      .toEqual(["批號", "品牌", "名稱"]);
  });

  it("久滯庫存比照序號品：品牌型號欄、型號成色篩選、總頁數", async () => {
    stubInventory();
    renderPage();
    await screen.findByText("SER-001");

    await userEvent.click(screen.getByRole("tab", { name: "久滯庫存" }));
    await screen.findByText(/第 \d+ \/ \d+ 頁/);
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent).slice(0, 4))
      .toEqual(["序號碼", "品牌", "品名", "型號"]);
    expect(screen.getByLabelText("型號")).toBeTruthy();
    expect(screen.getByLabelText("成色")).toBeTruthy();
  });

  it("刪除一般商品：確認後打 DELETE；被擋下時照實顯示後端原因", async () => {
    loginManager();
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      if (method === "DELETE" && new URL(url).pathname.startsWith("/api/v1/catalog-products/")) {
        calls.push(url);
        return json({ detail: "這件有採購紀錄，不能刪除（進貨帳要留著）" }, 409);
      }
      return route(url) ?? json(null, 404);
    }));
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "一般商品" }));
    await screen.findByText("SKU-9");
    await userEvent.click(screen.getAllByRole("button", { name: "編輯" })[0]);
    await userEvent.click(await screen.findByRole("button", { name: "刪除這個商品" }));
    // 站內確認視窗（不是瀏覽器的 confirm）
    const dialog = await screen.findByRole("dialog", { name: "刪除商品" });
    expect(calls).toEqual([]);
    await userEvent.click(within(dialog).getByRole("button", { name: "刪除" }));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(await screen.findByText(/採購紀錄/)).toBeTruthy();
  });

  it("刪除：取消確認不送出（誤按不該讓商品消失）", async () => {
    loginManager();
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      if (method === "DELETE") {
        calls.push(url);
        return json(null, 204);
      }
      return route(url) ?? json(null, 404);
    }));
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "一般商品" }));
    await screen.findByText("SKU-9");
    await userEvent.click(screen.getAllByRole("button", { name: "編輯" })[0]);
    await userEvent.click(await screen.findByRole("button", { name: "刪除這個商品" }));
    const dialog = await screen.findByRole("dialog", { name: "刪除商品" });
    await userEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(calls).toEqual([]);
  });

  it("編輯商品：改品名送 PATCH；商品編號不給改", async () => {
    loginManager();
    let patched = "";
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      if (method === "PATCH" && new URL(url).pathname.startsWith("/api/v1/catalog-products/")) {
        patched =
          input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
        return json({ ...CATALOG[0], name: "高山瓦斯罐" });
      }
      return route(url) ?? json(null, 404);
    }));
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "一般商品" }));
    await screen.findByText("瓦斯罐");
    await userEvent.click(screen.getAllByRole("button", { name: "編輯" })[0]);
    const dialog = await screen.findByRole("dialog", { name: "編輯商品" });
    // 條碼不可改：視窗裡沒有可編輯的商品編號欄位
    expect(within(dialog).queryByLabelText("商品編號")).toBeNull();
    const nameInput = within(dialog).getByLabelText("品名");
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, "高山瓦斯罐");
    await userEvent.click(within(dialog).getByRole("button", { name: "儲存" }));
    await waitFor(() => expect(patched).toContain("高山瓦斯罐"));
    expect(JSON.parse(patched).sku).toBeUndefined();
  });

  it("停售：確認後送 is_active=false；停售中的顯示徽章並可恢復上架", async () => {
    loginManager();
    let patched = "";
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      if (method === "PATCH" && new URL(url).pathname.startsWith("/api/v1/catalog-products/")) {
        patched =
          input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
        return json({ ...CATALOG[0], is_active: false });
      }
      if (new URL(url).pathname === "/api/v1/catalog-products") {
        return json([{ ...CATALOG[0], is_active: patched === "" }]);
      }
      return route(url) ?? json(null, 404);
    }));
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "一般商品" }));
    await screen.findByText("瓦斯罐");
    await userEvent.click(screen.getAllByRole("button", { name: "編輯" })[0]);
    await userEvent.click(await screen.findByRole("button", { name: "停售" }));
    await waitFor(() => expect(JSON.parse(patched).is_active).toBe(false));
    expect(await screen.findByText("已停售")).toBeTruthy();
    // 再開一次編輯視窗，狀態區要變成「恢復上架」
    await userEvent.click(screen.getAllByRole("button", { name: "編輯" })[0]);
    expect(await screen.findByRole("button", { name: "恢復上架" })).toBeTruthy();
  });
});
