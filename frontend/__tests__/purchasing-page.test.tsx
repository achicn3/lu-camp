// @vitest-environment jsdom
// /purchasing 採購工作台：採購單清單 + 收貨、建單、供應商建檔、低庫存提醒。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const nav = vi.hoisted(() => ({
  push: vi.fn(),
  search: "",
  id: "7",
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: nav.push }),
  useSearchParams: () => new URLSearchParams(nav.search),
  useParams: () => ({ id: nav.id }),
}));

import PurchaseOrderPage from "@/app/(authed)/purchasing/[id]/page";
import NewPurchaseOrderPage from "@/app/(authed)/purchasing/new/page";
import PurchasingPage from "@/app/(authed)/purchasing/page";
import {
  clearPendingCatalogCreate,
  clearPendingReceive,
  loadPendingReceive,
} from "@/lib/idempotency";
import { clearToken, setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function loginAs(role: "MANAGER" | "CLERK") {
  setToken(fakeJwt({ sub: "1", role, store_id: 1 }));
}

function json(data: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

const SUPPLIER = {
  id: 5,
  store_id: 1,
  name: "山林供應商",
  contact: "0911-222-333",
  tax_id: "12345678",
  is_active: true,
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
  incoming_qty: 0,
};

const ORDERED_PO = {
  id: 7,
  store_id: 1,
  supplier_id: 5,
  supplier_name: "山林供應商",
  status: "ORDERED",
  ordered_by: 1,
  ordered_at: "2026-06-20T01:00:00Z",
  received_at: null,
  received_by: null,
  created_at: "2026-06-20T01:00:00Z",
  updated_at: "2026-06-20T01:00:00Z",
  total_cost: "600",
  lines: [
    { id: 1, catalog_product_id: 42, qty: 10, received_qty: 0, unit_cost: "60", line_total: "600" },
  ],
  receipts: [],
};

type FetchRoute = (url: string, init: RequestInit) => Response | Promise<Response> | null;

function headerVal(init?: RequestInit, name = "idempotency-key"): string | undefined {
  const h = init?.headers;
  if (h == null) return undefined;
  const entries =
    h instanceof Headers
      ? Object.fromEntries(h)
      : Array.isArray(h)
        ? Object.fromEntries(h)
        : (h as Record<string, string>);
  const lower = Object.fromEntries(
    Object.entries(entries).map(([k, v]) => [k.toLowerCase(), v]),
  );
  return lower[name];
}

function stubFetch(route: FetchRoute) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      const body =
        input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
      const headers = input instanceof Request ? input.headers : init?.headers;
      const resp = await route(url, { method, body, headers } as RequestInit);
      if (resp) return resp;
      throw new Error(`unmatched fetch: ${method} ${url}`);
    }),
  );
}

function renderWith(node: ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(node, { wrapper });
}

/** 採購單列表頁（滿版清單＋供應商分頁）。 */
function renderPage() {
  return renderWith(<PurchasingPage />);
}

/** 建立採購單頁；search 例："reorder=42"。 */
function renderNew(search = "") {
  nav.search = search;
  return renderWith(<NewPurchaseOrderPage />);
}

/** 採購單明細頁；search 例："receive=1"。 */
function renderDetail(id = 7, search = "") {
  nav.id = String(id);
  nav.search = search;
  return renderWith(<PurchaseOrderPage />);
}

/** 明細頁的採購單讀取：/purchase-orders/{id}（不含 /receive、/cancel 等子路徑）。 */
function isPoDetail(url: string, id = 7): boolean {
  return new URL(url).pathname.endsWith(`/purchase-orders/${id}`);
}

afterEach(() => {
  cleanup();
  nav.search = "";
  nav.id = "7";
  clearToken();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  clearPendingCatalogCreate(1);
  clearPendingReceive(ORDERED_PO.id); // 避免收貨 pending 冪等狀態跨測試殘留
  try {
    globalThis.localStorage?.clear();
  } catch {
    // jsdom 無 localStorage 時忽略
  }
});

describe("/purchasing 列表頁", () => {
  it("滿版列出採購單，右上有建立採購單入口，可直接收貨", async () => {
    loginAs("CLERK");
    stubFetch((url) => {
      if (url.includes("/catalog-products") && url.includes("low_stock=true")) return json([CATALOG]);
      if (url.includes("/purchase-orders/count")) return json({ count: 1 });
      if (url.includes("/purchase-orders")) return json([ORDERED_PO]);
      return null;
    });
    renderPage();

    // 採購單清單以文字顯示供應商名、到貨進度與狀態。
    expect(await screen.findByText("山林供應商")).toBeTruthy();
    expect(screen.getByText("0 / 10")).toBeTruthy();
    expect(screen.getByText("已下單")).toBeTruthy();
    const receive = screen.getByRole("link", { name: "收貨入庫" });
    expect(receive.getAttribute("href")).toBe("/purchasing/7?receive=1");
    const create = screen.getByRole("link", { name: "＋ 建立採購單" });
    expect(create.getAttribute("href")).toBe("/purchasing/new");
  });

  it("低庫存提醒是頂端一條：顯示幾項，展開看現量與在途，可一次全部帶入", async () => {
    loginAs("CLERK");
    const covered = { ...CATALOG, id: 43, name: "營燈", quantity_on_hand: 1, reorder_point: 5, incoming_qty: 8 };
    stubFetch((url) => {
      if (url.includes("/catalog-products") && url.includes("low_stock=true"))
        return json([CATALOG, covered]);
      if (url.includes("/purchase-orders/count")) return json({ count: 0 });
      if (url.includes("/purchase-orders")) return json([]);
      return null;
    });
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText(/低於補貨點/)).toBeTruthy();
    expect(screen.getByText(/其中 1 項已在途/)).toBeTruthy();
    // 在途已足的不列入「全部帶入」，避免重複下單。
    expect(
      screen.getByRole("link", { name: "全部帶入建立採購單" }).getAttribute("href"),
    ).toBe("/purchasing/new?reorder=42");
    await user.click(screen.getByRole("button", { name: "查看" }));
    expect(screen.getAllByText(/現量 1 \/ 補貨點 5/)).toHaveLength(2);
    expect(screen.getByText(/待到貨 8/)).toBeTruthy();
    expect(screen.getByText(/在途已足/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "補貨 瓦斯罐" }).getAttribute("href")).toBe(
      "/purchasing/new?reorder=42",
    );
  });

  it("沒有低庫存就不佔版面", async () => {
    loginAs("CLERK");
    stubFetch((url) => {
      if (url.includes("/catalog-products")) return json([]);
      if (url.includes("/purchase-orders/count")) return json({ count: 0 });
      if (url.includes("/purchase-orders")) return json([]);
      return null;
    });
    renderPage();
    expect(await screen.findByText("尚無符合的採購單。")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "低庫存提醒" })).toBeNull();
  });

  it("點採購單單號進明細頁", async () => {
    loginAs("CLERK");
    stubFetch((url) => {
      if (url.includes("/catalog-products")) return json([]);
      if (url.includes("/purchase-orders/count")) return json({ count: 1 });
      if (url.includes("/purchase-orders")) return json([ORDERED_PO]);
      return null;
    });
    renderPage();
    expect((await screen.findByRole("link", { name: "#7" })).getAttribute("href")).toBe(
      "/purchasing/7",
    );
  });

  it("採購單顯示下單當下的供應商名快照（改名/停用不改寫歷史）", async () => {
    loginAs("CLERK");
    // 供應商目前叫「新名」，但採購單快照仍是下單當下的「舊名商」→ 歷史顯示快照、不被改名回溯改寫。
    const po = { ...ORDERED_PO, id: 8, supplier_id: 9, supplier_name: "舊名商" };
    stubFetch((url) => {
      if (url.includes("/suppliers")) return json([{ ...SUPPLIER, id: 9, name: "新名" }]);
      if (url.includes("/catalog-products")) return json([]);
      if (url.includes("/purchase-orders/count")) return json({ count: 1 });
      if (url.includes("/purchase-orders")) return json([po]);
      return null;
    });
    renderPage();

    expect(await screen.findByText("舊名商")).toBeTruthy(); // PO 快照名
    expect(screen.queryByText("新名")).toBeNull(); // 不顯示供應商目前名
    expect(screen.queryByText("#9")).toBeNull(); // 不掉成 fallback 數字 id
  });

  it("採購單狀態篩選會帶上 status 查詢參數", async () => {
    loginAs("CLERK");
    const poUrls: string[] = [];
    stubFetch((url) => {
      if (url.includes("/catalog-products")) return json([]);
      if (url.includes("/purchase-orders/count")) return json({ count: 1 });
      if (url.includes("/purchase-orders")) {
        poUrls.push(url);
        return json([ORDERED_PO]);
      }
      return null;
    });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("已下單"); // 預設只看「待收貨（ORDERED）」→ 帶 status=ORDERED
    expect(poUrls.some((u) => u.includes("status=ORDERED"))).toBe(true);

    // 切「全部」→ 不帶 status 參數（看所有採購單）。
    await user.click(screen.getByRole("button", { name: "全部" }));
    await waitFor(() =>
      expect(poUrls.some((u) => u.includes("purchase-orders") && !u.includes("status="))).toBe(
        true,
      ),
    );
  });

  it("採購單以單號/供應商搜尋帶上 q 參數", async () => {
    loginAs("CLERK");
    const poUrls: string[] = [];
    stubFetch((url) => {
      if (url.includes("/catalog-products")) return json([]);
      if (url.includes("/purchase-orders/count")) return json({ count: 1 });
      if (url.includes("/purchase-orders")) {
        poUrls.push(url);
        return json([ORDERED_PO]);
      }
      return null;
    });
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("已下單");
    await user.type(screen.getByLabelText("採購單搜尋"), "山林");
    await user.click(screen.getByRole("button", { name: "搜尋" }));
    await waitFor(() =>
      expect(poUrls.some((u) => decodeURIComponent(u).includes("q=山林"))).toBe(true),
    );
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

  it("供應商可編輯名稱、可停用（列出含停用者）", async () => {
    loginAs("MANAGER");
    let patchBody: string | null = null;
    let deactivated = false;
    const inactive = { ...SUPPLIER, id: 9, name: "已停用商", is_active: false };
    stubFetch((url, init) => {
      if (url.includes("/suppliers/5/deactivate") && init.method === "POST") {
        deactivated = true;
        return json({ ...SUPPLIER, is_active: false });
      }
      if (url.match(/\/suppliers\/5$/) && init.method === "PATCH") {
        patchBody = init.body as string;
        return json({ ...SUPPLIER, name: "改後名" });
      }
      // 管理清單帶 include_inactive → 含停用者
      if (url.includes("/suppliers")) return json([SUPPLIER, inactive]);
      if (url.includes("/catalog-products")) return json([]);
      if (url.includes("/purchase-orders")) return json([]);
      return null;
    });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "供應商" }));
    // 清單含停用者（already 已停用 badge）
    expect(await screen.findByText("已停用商")).toBeTruthy();
    expect(screen.getByText("已停用")).toBeTruthy();

    // 編輯山林供應商 → 改名 → PATCH
    const rows = screen.getAllByRole("row");
    const active = rows.find((r) => within(r).queryByText("山林供應商"));
    const inactiveRow = rows.find((r) => within(r).queryByText("已停用商"));
    const deactivateButton = within(active as HTMLElement).getByRole("button", { name: "停用" });
    const activateButton = within(inactiveRow as HTMLElement).getByRole("button", { name: "啟用" });
    expect(deactivateButton.classList.contains("pur-supplier-state-btn")).toBe(true);
    expect(activateButton.classList.contains("pur-supplier-state-btn")).toBe(true);
    await user.click(within(active as HTMLElement).getByRole("button", { name: "編輯" }));
    const nameInput = await screen.findByLabelText("編輯供應商名稱");
    await user.clear(nameInput);
    await user.type(nameInput, "改後名");
    await user.click(screen.getByRole("button", { name: "儲存" }));
    await waitFor(() => expect(patchBody).not.toBeNull());
    // 稀疏 PATCH：只改名 → body 只含 name，未動的 contact/tax_id 不重送（不覆蓋並發修改）。
    const parsed = JSON.parse(patchBody as unknown as string);
    expect(parsed.name).toBe("改後名");
    expect("contact" in parsed).toBe(false);
    expect("tax_id" in parsed).toBe(false);

    // 停用山林供應商 → deactivate 端點
    await user.click(within(active as HTMLElement).getByRole("button", { name: "停用" }));
    await waitFor(() => expect(deactivated).toBe(true));
  });
});

describe("/purchasing/new 建立採購單", () => {
  async function pickSupplier(user: ReturnType<typeof userEvent.setup>) {
    const supplierInput = screen.getByLabelText("供應商");
    await user.click(supplierInput);
    await user.type(supplierInput, "山林");
    await user.click(await screen.findByRole("option", { name: "山林供應商" }));
  }

  it("builds a purchase order from a searched catalog product, then opens its detail page", async () => {
    loginAs("CLERK");
    let createdBody: string | null = null;
    stubFetch((url, init) => {
      if (url.includes("/suppliers")) return json([SUPPLIER]);
      if (url.includes("/brands")) return json([]);
      if (url.includes("/catalog-products")) return json([CATALOG]);
      if (url.includes("/purchase-orders") && init.method === "POST") {
        createdBody = init.body as string;
        return json(ORDERED_PO, 201);
      }
      return null;
    });
    const user = userEvent.setup();
    renderNew();

    await pickSupplier(user);
    await user.type(screen.getByLabelText("搜尋一般商品"), "瓦斯");
    await user.click(await screen.findByRole("button", { name: /瓦斯罐/ }));
    await user.type(screen.getByLabelText("進貨單價 瓦斯罐"), "60");
    await user.click(screen.getByRole("button", { name: "送出採購" }));

    await waitFor(() => expect(createdBody).not.toBeNull());
    const parsed = JSON.parse(createdBody as unknown as string);
    expect(parsed.supplier_id).toBe(5);
    expect(parsed.lines).toEqual([{ catalog_product_id: 42, qty: 1, unit_cost: "60" }]);
    expect(parsed.submit).toBe(true);
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/purchasing/7"));
  });

  it("搜尋結果不顯示 SKU，改顯示品牌、售價與現量", async () => {
    loginAs("CLERK");
    stubFetch((url) => {
      if (url.includes("/brands")) return json([{ id: 2, name: "Snow Peak" }]);
      if (url.includes("/catalog-products"))
        return json([{ ...CATALOG, sku: "AUTO-XYZ", brand_id: 2 }]);
      return null;
    });
    const user = userEvent.setup();
    renderNew();
    await user.type(screen.getByLabelText("搜尋一般商品"), "瓦斯");
    const hit = await screen.findByRole("button", { name: /瓦斯罐/ });
    await waitFor(() => expect(hit.textContent).toContain("Snow Peak"));
    expect(hit.textContent).toContain("售價 120");
    expect(screen.queryByText(/AUTO-XYZ/)).toBeNull();
  });

  it("新增商品不出現 SKU 欄位：送出 sku=null 由系統產生，並直接加入採購明細", async () => {
    loginAs("MANAGER");
    const created = {
      ...CATALOG,
      id: 88,
      sku: "AUTO-A1B2C3D4E5F6",
      name: "首次採購營繩",
      unit_price: "250",
      quantity_on_hand: 0,
      reorder_point: 0,
    };
    let createdBody: string | null = null;
    stubFetch((url, init) => {
      if (url.includes("/brands")) return json([]);
      if (url.includes("/catalog-products") && init.method === "POST") {
        createdBody = init.body as string;
        return json(created, 201);
      }
      if (url.includes("/catalog-products")) return json([]);
      return null;
    });
    const user = userEvent.setup();
    renderNew();

    await user.type(screen.getByLabelText("搜尋一般商品"), "首次採購營繩");
    await user.click(screen.getByRole("button", { name: "＋ 新增商品" }));
    expect((screen.getByLabelText("一般商品名稱") as HTMLInputElement).value).toBe("首次採購營繩");
    expect(screen.queryByLabelText("一般商品編號")).toBeNull();
    await user.type(screen.getByLabelText("一般商品售價"), "250");
    await user.click(screen.getByRole("button", { name: "建立並加入採購單" }));

    await waitFor(() => expect(createdBody).not.toBeNull());
    expect(JSON.parse(createdBody as unknown as string)).toEqual({
      sku: null,
      name: "首次採購營繩",
      unit_price: 250,
      reorder_point: 0,
    });
    expect(await screen.findByLabelText("進貨單價 首次採購營繩")).toBeTruthy();
    expect(screen.queryByText("AUTO-A1B2C3D4E5F6")).toBeNull();
  });

  it("選了型號，品名自動帶型號；店員自己改過的品名不覆蓋", async () => {
    loginAs("MANAGER");
    stubFetch((url, init) => {
      if (url.includes("/brands") && (init.method ?? "GET") === "GET")
        return json([{ id: 2, name: "Snow Peak" }]);
      if (url.includes("/product-models"))
        return json([
          { id: 3, brand_id: 2, name: "GST-120" },
          { id: 4, brand_id: 2, name: "GST-100" },
        ]);
      if (url.includes("/catalog-products")) return json([]);
      if (url.includes("/categories")) return json([]);
      return null;
    });
    const user = userEvent.setup();
    renderNew();

    await user.click(screen.getByRole("button", { name: "＋ 新增商品" }));
    const brand = screen.getByLabelText("品牌");
    await user.click(brand);
    await user.type(brand, "Snow");
    await user.click(await screen.findByRole("option", { name: "Snow Peak" }));
    const model = screen.getByLabelText("型號");
    await user.click(model);
    await user.type(model, "GST");
    await user.click(await screen.findByRole("option", { name: "GST-120" }));
    const name = screen.getByLabelText("一般商品名稱") as HTMLInputElement;
    expect(name.value).toBe("GST-120");

    // 店員改了品名之後再換型號，不可蓋掉他打的字。
    await user.clear(name);
    await user.type(name, "高山瓦斯爐");
    await user.click(screen.getByRole("button", { name: "清除已選的型號" }));
    const reopened = screen.getByLabelText("型號");
    await user.click(reopened);
    await user.type(reopened, "GST-100");
    await user.click(await screen.findByRole("option", { name: "GST-100" }));
    expect(name.value).toBe("高山瓦斯爐");
  });

  it("填進貨成本與毛利率就自動算出建議售價；成本與數量也帶進採購明細那一列", async () => {
    // 裁示 2026-09-16：毛利率逐件設定（預設取設定值 30%），建議售價含營業稅與行動支付
    // 手續費補償（與收購同一套算法，CLAUDE.md §7.9）。
    loginAs("MANAGER");
    const created = { ...CATALOG, id: 91, sku: "AUTO-COST01", name: "濾掛咖啡", unit_price: "80" };
    let createdBody: string | null = null;
    stubFetch((url, init) => {
      if (url.includes("/brands")) return json([]);
      if (url.includes("/settings"))
        return json({
          tax_rate: "0.05",
          purchase_default_margin_pct: 30,
          linepay_fee_pct: "0.0220",
          taiwanpay_fee_pct: "0.0000",
        });
      if (url.includes("/catalog-products") && init.method === "POST") {
        createdBody = init.body as string;
        return json(created, 201);
      }
      if (url.includes("/catalog-products")) return json([]);
      return null;
    });
    const user = userEvent.setup();
    renderNew();

    await user.type(screen.getByLabelText("搜尋一般商品"), "濾掛咖啡");
    await user.click(screen.getByRole("button", { name: "＋ 新增商品" }));

    // 毛利率預設帶設定值
    const margin = screen.getByLabelText("一般商品預估毛利率") as HTMLInputElement;
    await waitFor(() => expect(margin.value).toBe("30"));

    // 成本 50、毛利 30%、稅 5%、手續費 2.2%
    //   未稅目標 = 50 ÷ 0.7 = 71.43；含稅 = 75.0；補手續費 ÷ (1 − 0.022×1.05) = 76.8 → 77
    //   → 進位到 10 的倍數 → 80（ADR-023：系統帶出的上架售價一律 0 結尾）
    await user.type(screen.getByLabelText("一般商品進貨成本"), "50");
    const price = screen.getByLabelText("一般商品售價") as HTMLInputElement;
    await waitFor(() => expect(price.value).toBe("80"));
    const qty = screen.getByLabelText("一般商品採購數量");
    await user.clear(qty);
    await user.type(qty, "12");

    await user.click(screen.getByRole("button", { name: "建立並加入採購單" }));
    await waitFor(() => expect(createdBody).not.toBeNull());
    expect(JSON.parse(createdBody as unknown as string).unit_price).toBe(80);

    // 成本與數量自動帶進明細那一列，不必再打一次
    const costInput = (await screen.findByLabelText("進貨單價 濾掛咖啡")) as HTMLInputElement;
    expect(costInput.value).toBe("50");
    expect((screen.getByLabelText("數量 濾掛咖啡") as HTMLInputElement).value).toBe("12");
  });

  it("毛利率可逐件調整；改了就重算建議售價，手改售價後不再被蓋掉，可一鍵改回建議價", async () => {
    loginAs("MANAGER");
    stubFetch((url) => {
      if (url.includes("/brands")) return json([]);
      if (url.includes("/settings"))
        return json({
          tax_rate: "0.05",
          purchase_default_margin_pct: 30,
          linepay_fee_pct: "0.0000",
          taiwanpay_fee_pct: "0.0000",
        });
      if (url.includes("/catalog-products")) return json([]);
      return null;
    });
    const user = userEvent.setup();
    renderNew();

    await user.click(screen.getByRole("button", { name: "＋ 新增商品" }));
    await user.type(screen.getByLabelText("一般商品進貨成本"), "1000");

    const price = screen.getByLabelText("一般商品售價") as HTMLInputElement;
    await waitFor(() => expect(price.value).toBe("1500")); // 1000÷0.7×1.05

    const margin = screen.getByLabelText("一般商品預估毛利率") as HTMLInputElement;
    await user.clear(margin);
    await user.type(margin, "50");
    await waitFor(() => expect(price.value).toBe("2100")); // 1000÷0.5×1.05

    // 店員自己改過售價之後，再動毛利率也不該偷改他填的數字
    await user.clear(price);
    await user.type(price, "1999");
    await user.clear(margin);
    await user.type(margin, "40");
    await waitFor(() => expect(margin.value).toBe("40"));
    expect(price.value).toBe("1999");

    await user.click(screen.getByRole("button", { name: "改回建議售價" }));
    expect(price.value).toBe("1750"); // 1000÷0.6×1.05＝1750
  });

  it.each(["failed", "pending", "null", "blank", "nan"])("稅率 %s 時手填毛利也不能用零稅率推價", async (mode) => {
    loginAs("MANAGER");
    let release!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => { release = resolve; });
    const valid = { tax_rate: "0.05", purchase_default_margin_pct: 30, linepay_fee_pct: "0.022", taiwanpay_fee_pct: "0.01" };
    stubFetch((url) => {
      if (url.includes("/settings")) {
        if (mode === "pending") return pending;
        if (mode === "failed") return json({ detail: "unavailable" }, 500);
        return json({ ...valid, tax_rate: mode === "null" ? null : mode === "blank" ? "" : "invalid" });
      }
      return json([]);
    });
    renderNew();
    await userEvent.click(screen.getByRole("button", { name: "＋ 新增商品" }));
    await userEvent.type(screen.getByLabelText("一般商品進貨成本"), "1000");
    await userEvent.clear(screen.getByLabelText("一般商品預估毛利率"));
    await userEvent.type(screen.getByLabelText("一般商品預估毛利率"), "30");
    const price = screen.getByLabelText("一般商品售價") as HTMLInputElement;
    expect(price.value).toBe("");
    expect(screen.getByText(mode === "pending" ? /稅率設定載入中/ : /讀不到稅率設定/)).toBeTruthy();
    if (mode === "pending") {
      release(json(valid));
      // 手算 1000 / .7 * 1.05 / (1 - .022 * 1.05) = 1535.469...，整數 1535 → 進位 1540。
      // 這條守的是「不可用零稅率推價」：零稅率會得到 1430（進位 1430），與 1540 仍分得開。
      await waitFor(() => expect(price.value).toBe("1540"));
    } else {
      await userEvent.type(price, "1600");
      expect(price.value).toBe("1600");
    }
  });

  it.each(["30.5", "30abc", "100", "-1"])("毛利率 %s 不可截斷成整數推價", async (value) => {
    loginAs("MANAGER");
    stubFetch((url) => url.includes("/settings") ? json({ tax_rate: "0.05", purchase_default_margin_pct: 30, linepay_fee_pct: "0", taiwanpay_fee_pct: "0" }) : json([]));
    renderNew();
    await userEvent.click(screen.getByRole("button", { name: "＋ 新增商品" }));
    await userEvent.type(screen.getByLabelText("一般商品進貨成本"), "1000");
    await userEvent.clear(screen.getByLabelText("一般商品預估毛利率"));
    await userEvent.type(screen.getByLabelText("一般商品預估毛利率"), value);
    expect((screen.getByLabelText("一般商品售價") as HTMLInputElement).value).toBe("");
    expect(screen.getByText("毛利率請輸入 0–99 的整數")).toBeTruthy();
  });

  it("建立一般商品回應失敗後重試會沿用同一冪等鍵", async () => {
    loginAs("CLERK");
    const created = {
      ...CATALOG,
      id: 88,
      sku: "AUTO-A1B2C3D4E5F6",
      name: "重試建立營繩",
      unit_price: "250",
      quantity_on_hand: 0,
      reorder_point: 0,
    };
    const keys: (string | undefined)[] = [];
    stubFetch((url, init) => {
      if (url.includes("/brands")) return json([]);
      if (url.includes("/catalog-products") && init.method === "POST") {
        keys.push(headerVal(init));
        return keys.length === 1 ? json({ detail: "暫時無法確認建立結果" }, 500) : json(created, 201);
      }
      if (url.includes("/catalog-products")) return json([]);
      return null;
    });
    const user = userEvent.setup();
    renderNew();

    await user.type(screen.getByLabelText("搜尋一般商品"), "重試建立營繩");
    await user.click(screen.getByRole("button", { name: "＋ 新增商品" }));
    await user.type(screen.getByLabelText("一般商品售價"), "250");
    await user.click(screen.getByRole("button", { name: "建立並加入採購單" }));
    expect(await screen.findByText("暫時無法確認建立結果")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "重試並確認建立結果" }));
    expect(await screen.findByLabelText("進貨單價 重試建立營繩")).toBeTruthy();
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBeTruthy();
    expect(keys[1]).toBe(keys[0]);
  });

  it("建立一般商品回應不明後，重新進頁面會還原原請求並沿用冪等鍵", async () => {
    loginAs("CLERK");
    const created = {
      ...CATALOG,
      id: 89,
      sku: "AUTO-B1C2D3E4F5A6",
      name: "跨重掛營繩",
      unit_price: "260",
      quantity_on_hand: 0,
      reorder_point: 3,
    };
    const calls: { key: string | undefined; body: unknown }[] = [];
    stubFetch((url, init) => {
      if (url.includes("/brands")) return json([]);
      if (url.includes("/catalog-products") && init.method === "POST") {
        calls.push({ key: headerVal(init), body: JSON.parse(String(init.body)) });
        return calls.length === 1
          ? json({ detail: "暫時無法確認建立結果" }, 500)
          : json(created, 201);
      }
      if (url.includes("/catalog-products")) return json([]);
      return null;
    });
    const user = userEvent.setup();
    const firstMount = renderNew();

    await user.type(screen.getByLabelText("搜尋一般商品"), "跨重掛營繩");
    await user.click(screen.getByRole("button", { name: "＋ 新增商品" }));
    await user.type(screen.getByLabelText("一般商品售價"), "260");
    await user.clear(screen.getByLabelText("一般商品低庫存提醒點"));
    await user.type(screen.getByLabelText("一般商品低庫存提醒點"), "3");
    await user.click(screen.getByRole("button", { name: "建立並加入採購單" }));
    expect(await screen.findByText("暫時無法確認建立結果")).toBeTruthy();

    firstMount.unmount();
    renderNew();

    // 待確認的建立會自動攤開表單並帶回原內容。
    expect((await screen.findByLabelText("一般商品名稱") as HTMLInputElement).value).toBe(
      "跨重掛營繩",
    );
    expect((screen.getByLabelText("一般商品售價") as HTMLInputElement).value).toBe("260");
    expect(
      (screen.getByLabelText("一般商品低庫存提醒點") as HTMLInputElement).value,
    ).toBe("3");
    await user.click(screen.getByRole("button", { name: "重試並確認建立結果" }));

    expect(await screen.findByLabelText("進貨單價 跨重掛營繩")).toBeTruthy();
    expect(calls).toHaveLength(2);
    expect(calls[1]).toEqual(calls[0]);
  });

  it("建立一般商品尚未完成時不可儲存或送出採購單", async () => {
    loginAs("CLERK");
    let resolveProduct!: (response: Response) => void;
    const pendingProduct = new Promise<Response>((resolve) => {
      resolveProduct = resolve;
    });
    stubFetch((url, init) => {
      if (url.includes("/suppliers")) return json([SUPPLIER]);
      if (url.includes("/brands")) return json([]);
      if (url.includes("/catalog-products") && init.method === "POST") return pendingProduct;
      if (url.includes("/catalog-products")) {
        return json(new URL(url).searchParams.get("q") === "瓦斯" ? [CATALOG] : []);
      }
      return null;
    });
    const user = userEvent.setup();
    renderNew();

    await pickSupplier(user);
    const productSearch = screen.getByLabelText("搜尋一般商品");
    await user.type(productSearch, "瓦斯");
    await user.click(await screen.findByRole("button", { name: /瓦斯罐/ }));
    await user.type(screen.getByLabelText("進貨單價 瓦斯罐"), "60");

    await user.clear(productSearch);
    await user.type(productSearch, "首次採購營繩");
    await user.click(screen.getByRole("button", { name: "＋ 新增商品" }));
    await user.type(screen.getByLabelText("一般商品售價"), "250");
    await user.click(screen.getByRole("button", { name: "建立並加入採購單" }));
    expect((await screen.findByRole("button", { name: "建立中…" }) as HTMLButtonElement).disabled).toBe(true);

    expect((screen.getByRole("button", { name: "存草稿" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "送出採購" }) as HTMLButtonElement).disabled).toBe(true);

    resolveProduct(
      json({
        ...CATALOG,
        id: 88,
        sku: "AUTO-A1B2C3D4E5F6",
        name: "首次採購營繩",
        unit_price: "250",
        quantity_on_hand: 0,
      }, 201),
    );
    expect(await screen.findByLabelText("進貨單價 首次採購營繩")).toBeTruthy();
  });

  it("建單供應商 combobox 以伺服器端搜尋（帶 q、只搜啟用中）", async () => {
    loginAs("CLERK");
    const supplierUrls: string[] = [];
    stubFetch((url) => {
      if (url.includes("/suppliers")) {
        supplierUrls.push(url);
        return json([SUPPLIER]);
      }
      return json([]);
    });
    const user = userEvent.setup();
    renderNew();

    await pickSupplier(user);
    // combobox 查詢伺服器端帶 q，且不帶 include_inactive=true（只搜啟用中；不受前端預載上限影響）。
    await waitFor(() =>
      expect(
        supplierUrls.some(
          (u) => decodeURIComponent(u).includes("q=山林") && !u.includes("include_inactive=true"),
        ),
      ).toBe(true),
    );
  });

  it("存草稿以 submit=false 建立採購單", async () => {
    loginAs("CLERK");
    let createdBody: string | null = null;
    stubFetch((url, init) => {
      if (url.includes("/suppliers")) return json([SUPPLIER]);
      if (url.includes("/brands")) return json([]);
      if (url.includes("/catalog-products")) return json([CATALOG]);
      if (url.includes("/purchase-orders") && init.method === "POST") {
        createdBody = init.body as string;
        return json({ ...ORDERED_PO, status: "DRAFT" }, 201);
      }
      return null;
    });
    const user = userEvent.setup();
    renderNew();

    await pickSupplier(user);
    await user.type(screen.getByLabelText("搜尋一般商品"), "瓦斯");
    await user.click(await screen.findByRole("button", { name: /瓦斯罐/ }));
    await user.type(screen.getByLabelText("進貨單價 瓦斯罐"), "60");
    await user.click(screen.getByRole("button", { name: "存草稿" }));

    await waitFor(() => expect(createdBody).not.toBeNull());
    expect(JSON.parse(createdBody as unknown as string).submit).toBe(false);
  });

  it("從低庫存「補貨」進來：該品已在明細，數量預設補到補貨點", async () => {
    loginAs("CLERK");
    stubFetch((url) => {
      if (url.includes("/brands")) return json([]);
      if (new URL(url).pathname.endsWith("/catalog-products/42")) return json(CATALOG);
      if (url.includes("/catalog-products")) return json([]);
      return null;
    });
    renderNew("reorder=42");

    expect(await screen.findByLabelText("進貨單價 瓦斯罐")).toBeTruthy();
    // 現量 1、補貨點 5 → 預設採購 4。
    expect((screen.getByLabelText("數量 瓦斯罐") as HTMLInputElement).value).toBe("4");
    expect(screen.getByLabelText("供應商")).toBeTruthy();
  });
});

describe("/purchasing/[id] 採購單明細", () => {
  function detailRoutes(po: object, extra: FetchRoute = () => null): FetchRoute {
    return (url, init) => {
      const hit = extra(url, init);
      if (hit) return hit;
      if (url.includes("/brands")) return json([]);
      if (new URL(url).pathname.endsWith("/catalog-products/42")) return json(CATALOG);
      if (isPoDetail(url)) return json(po);
      return null;
    };
  }

  it("顯示明細品名、合計，並可回到列表", async () => {
    loginAs("CLERK");
    stubFetch(detailRoutes(ORDERED_PO));
    renderDetail();

    expect(await screen.findByText("採購單 #7")).toBeTruthy();
    // 明細以品名顯示（非 #42）。
    expect(await screen.findByText("瓦斯罐", { selector: "td" })).toBeTruthy();
    expect(screen.getByText("合計")).toBeTruthy();
    expect(screen.getByRole("link", { name: "← 回採購單列表" }).getAttribute("href")).toBe(
      "/purchasing",
    );
  });

  it("草稿詳情顯示建立時間，不把尚未送出的時間稱為下單時間；可直接送出", async () => {
    loginAs("CLERK");
    let submitted = false;
    const draft = {
      ...ORDERED_PO,
      status: "DRAFT",
      created_at: "2026-06-19T01:00:00Z",
      ordered_at: "2099-01-01T01:00:00Z",
    };
    stubFetch(
      detailRoutes(draft, (url, init) => {
        if (url.includes("/submit") && init.method === "POST") {
          submitted = true;
          return json({ ...draft, status: "ORDERED" });
        }
        return null;
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    expect(await screen.findByText("建立時間")).toBeTruthy();
    expect(screen.queryByText("下單時間")).toBeNull();
    expect(screen.queryByText(/2099/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "送出採購" }));
    await waitFor(() => expect(submitted).toBe(true));
  });

  it("receives a purchase order after confirmation", async () => {
    loginAs("CLERK");
    const received = {
      ...ORDERED_PO,
      status: "RECEIVED",
      received_at: "2026-06-20T02:00:00Z",
      lines: [{ ...ORDERED_PO.lines[0], received_qty: 10 }],
    };
    let receivePosted = false;
    stubFetch((url, init) => {
      if (url.includes("/receive") && init.method === "POST") {
        receivePosted = true;
        return json({ receipt_id: 1, purchase_order: received });
      }
      if (url.includes("/brands")) return json([]);
      if (new URL(url).pathname.endsWith("/catalog-products/42")) return json(CATALOG);
      if (isPoDetail(url)) return json(receivePosted ? received : ORDERED_PO);
      return null;
    });
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "收貨入庫" }));
    await user.click(await screen.findByRole("button", { name: "確認收貨" }));

    await waitFor(() => expect(receivePosted).toBe(true));
    expect(await screen.findByText("已收貨", { selector: "span.inv-badge" })).toBeTruthy();
    // 收過貨的品項可以直接印標籤（條碼由系統產生）。
    expect(await screen.findByRole("button", { name: "印標籤 瓦斯罐" })).toBeTruthy();
  });

  it("從列表按「收貨入庫」進來（?receive=1）直接打開收貨對話框", async () => {
    loginAs("CLERK");
    stubFetch(detailRoutes(ORDERED_PO));
    renderDetail(7, "receive=1");
    expect(await screen.findByRole("dialog", { name: "確認收貨" })).toBeTruthy();
  });

  it("尚未收貨的品項不顯示印標籤", async () => {
    loginAs("CLERK");
    stubFetch(detailRoutes(ORDERED_PO));
    renderDetail();
    await screen.findByText("瓦斯罐", { selector: "td" });
    expect(screen.queryByRole("button", { name: "印標籤 瓦斯罐" })).toBeNull();
  });

  it("收貨對話框發票草稿不跨次殘留（取消/重開即清空）", async () => {
    loginAs("CLERK");
    stubFetch(detailRoutes(ORDERED_PO));
    const user = userEvent.setup();
    renderDetail();

    // 開啟 → 打半張發票 → 取消
    await user.click(await screen.findByRole("button", { name: "收貨入庫" }));
    const numberInput = await screen.findByLabelText("發票號碼");
    await user.type(numberInput, "AB12345678");
    const dialog = screen.getByRole("dialog", { name: "確認收貨" });
    await user.click(within(dialog).getByRole("button", { name: "取消" }));

    // 重開 → 草稿必須清空（登錄不可覆寫，殘留誤登難以回復；Codex 第一輪）
    await user.click(await screen.findByRole("button", { name: "收貨入庫" }));
    const reopened = await screen.findByLabelText("發票號碼");
    expect((reopened as HTMLInputElement).value).toBe("");
  });

  it("分批收貨：送出各明細本次實收量", async () => {
    loginAs("CLERK");
    let receiveBody: string | null = null;
    stubFetch(
      detailRoutes(ORDERED_PO, (url, init) => {
        if (url.includes("/receive") && init.method === "POST") {
          receiveBody = init.body as string;
          return json({ receipt_id: 1, purchase_order: { ...ORDERED_PO, status: "PARTIAL" } });
        }
        return null;
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "收貨入庫" }));
    // 待收預設帶入 10；改為本次只收 4。
    const qtyInput = await screen.findByLabelText("本次實收 瓦斯罐");
    await user.clear(qtyInput);
    await user.type(qtyInput, "4");
    await user.click(screen.getByRole("button", { name: "確認收貨" }));

    await waitFor(() => expect(receiveBody).not.toBeNull());
    const parsed = JSON.parse(receiveBody as unknown as string);
    expect(parsed.lines).toEqual([{ line_id: 1, qty: 4 }]);
  });

  it("收貨回應遺失：以原 body＋原鍵重播和解，再以新鍵收剩餘", async () => {
    loginAs("CLERK");
    const calls: { key: string | undefined; lines: unknown }[] = [];
    let firstDone = false;
    stubFetch(
      detailRoutes(ORDERED_PO, (url, init) => {
        if (url.includes("/receive") && init.method === "POST") {
          calls.push({
            key: headerVal(init),
            lines: JSON.parse(String(init.body)).lines,
          });
          if (!firstDone) {
            firstDone = true; // 模擬「後端已提交但回應遺失」：先回 503（非可丟棄）
            return json({ detail: "服務暫時無法使用" }, 503);
          }
          return json({ receipt_id: 1, purchase_order: { ...ORDERED_PO, status: "PARTIAL" } });
        }
        return null;
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    // 1) 收 3 → 回應遺失（503）：鍵＋原 body 已持久化、未清（非可丟棄）
    await user.click(await screen.findByRole("button", { name: "收貨入庫" }));
    const qty = await screen.findByLabelText("本次實收 瓦斯罐");
    await user.clear(qty);
    await user.type(qty, "3");
    await user.click(screen.getByRole("button", { name: "確認收貨" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].lines).toEqual([{ line_id: 1, qty: 3 }]);
    expect(loadPendingReceive(ORDERED_PO.id)).not.toBeNull();
    const firstKey = calls[0].key;

    // 2) 對話框仍開；店員誤改輸入 7 → 但應先以「原 body(3)＋原鍵」重播和解（非送出 7）
    await user.clear(qty);
    await user.type(qty, "7");
    await user.click(screen.getByRole("button", { name: "確認收貨" }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1].key).toBe(firstKey); // 原鍵
    expect(calls[1].lines).toEqual([{ line_id: 1, qty: 3 }]); // 原 body，非 7
    await waitFor(() => expect(loadPendingReceive(ORDERED_PO.id)).toBeNull()); // 和解後清鍵
    expect(await screen.findByText(/已為您同步/)).toBeTruthy(); // 復原提示

    // 3) 下一批以新鍵收剩餘（重開對話框）
    await user.click(await screen.findByRole("button", { name: "收貨入庫" }));
    const qty2 = await screen.findByLabelText("本次實收 瓦斯罐");
    await user.clear(qty2);
    await user.type(qty2, "7");
    await user.click(screen.getByRole("button", { name: "確認收貨" }));
    await waitFor(() => expect(calls).toHaveLength(3));
    expect(calls[2].key).not.toBe(firstKey); // 新鍵
    expect(calls[2].lines).toEqual([{ line_id: 1, qty: 7 }]);
  });

  it("重複發票 409 會清除 pending，修正後以新鍵和新發票重送", async () => {
    loginAs("CLERK");
    const calls: {
      key: string | undefined;
      invoice: string | undefined;
      net: string | undefined;
      tax: string | undefined;
      total: string | undefined;
    }[] = [];
    stubFetch(
      detailRoutes(ORDERED_PO, (url, init) => {
        if (url.includes("/receive") && init.method === "POST") {
          const body = JSON.parse(String(init.body));
          const invoice = body.invoice?.invoice_number as string | undefined;
          calls.push({
            key: headerVal(init),
            invoice,
            net: body.invoice?.invoice_net,
            tax: body.invoice?.invoice_tax,
            total: body.invoice?.invoice_total,
          });
          if (invoice === "AB12345678") {
            return json(
              { detail: "此發票號碼（同日期）已登錄於其他採購單，不可重複入帳" },
              409,
              { "X-Lu-Camp-Error-Code": "DUPLICATE_INPUT_INVOICE" },
            );
          }
          return json({ receipt_id: 2, purchase_order: { ...ORDERED_PO, status: "PARTIAL" } });
        }
        return null;
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "收貨入庫" }));
    await user.clear(await screen.findByLabelText("本次實收 瓦斯罐"));
    await user.type(screen.getByLabelText("本次實收 瓦斯罐"), "3");
    await user.type(screen.getByLabelText("發票號碼"), "AB12345678");
    await user.type(screen.getByLabelText("發票日期"), "2026-07-11");
    await user.type(screen.getByLabelText("發票未稅金額"), "999");
    await user.type(screen.getByLabelText("發票稅額"), "51");
    await user.type(screen.getByLabelText("發票含稅金額"), "1050");
    await user.click(screen.getByRole("button", { name: "確認收貨" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toMatchObject({ net: "999", tax: "51", total: "1050" });
    expect(loadPendingReceive(ORDERED_PO.id)).toBeNull();

    await user.clear(screen.getByLabelText("發票號碼"));
    await user.type(screen.getByLabelText("發票號碼"), "CD87654321");
    await user.click(screen.getByRole("button", { name: "確認收貨" }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls.map((call) => call.invoice)).toEqual(["AB12345678", "CD87654321"]);
    expect(calls[1].key).not.toBe(calls[0].key);
  });

  it("收貨數量含小數時拒絕送出，不可用 parseInt 靜默截斷", async () => {
    loginAs("CLERK");
    let receiveCalled = false;
    stubFetch(
      detailRoutes(ORDERED_PO, (url, init) => {
        if (url.includes("/receive") && init.method === "POST") {
          receiveCalled = true;
          return json({ receipt_id: 1, purchase_order: ORDERED_PO });
        }
        return null;
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "收貨入庫" }));
    const qty = await screen.findByLabelText("本次實收 瓦斯罐");
    await user.clear(qty);
    await user.type(qty, "1.5");
    await user.click(screen.getByRole("button", { name: "確認收貨" }));

    expect(await screen.findByText("本次實收量必須為正整數")).toBeTruthy();
    expect(receiveCalled).toBe(false);
    expect(loadPendingReceive(ORDERED_PO.id)).toBeNull();
  });

  it("已下單可取消（呼叫 cancel 端點）", async () => {
    loginAs("CLERK");
    let cancelled = false;
    stubFetch(
      detailRoutes(ORDERED_PO, (url, init) => {
        if (url.includes("/cancel") && init.method === "POST") {
          cancelled = true;
          return json({ ...ORDERED_PO, status: "CANCELLED" });
        }
        return null;
      }),
    );
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "取消採購單" }));
    await waitFor(() => expect(cancelled).toBe(true));
  });
});
