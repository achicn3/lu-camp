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
    // 採購單清單以文字顯示供應商名（常駐）。
    expect(await screen.findByText("山林供應商")).toBeTruthy();
    expect(screen.getByRole("button", { name: "收貨入庫" })).toBeTruthy();
    expect(screen.getByText("已下單")).toBeTruthy();

    // 建立採購單面板預設收合；點「＋ 建立採購單」展開後才有供應商 combobox
    // （欄位改為「查無即建」combobox，與收購頁品牌一致）。
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "＋ 建立採購單" }));
    expect(await screen.findByLabelText("供應商")).toBeTruthy();
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

  it("收貨對話框發票草稿不跨單殘留（取消/重開即清空）", async () => {
    loginAs("CLERK");
    stubFetch((url) => {
      if (url.includes("/suppliers")) return json([SUPPLIER]);
      if (url.includes("/catalog-products")) return json([]);
      if (url.includes("/purchase-orders")) return json([ORDERED_PO]);
      return null;
    });
    const user = userEvent.setup();
    renderPage();

    // 開啟 → 打半張發票 → 取消
    await user.click(await screen.findByRole("button", { name: "收貨入庫" }));
    const numberInput = await screen.findByLabelText("發票號碼");
    await user.type(numberInput, "AB12345678");
    await user.click(screen.getByRole("button", { name: "取消" }));

    // 重開 → 草稿必須清空（登錄不可覆寫，殘留誤登難以回復；Codex 第一輪）
    await user.click(await screen.findByRole("button", { name: "收貨入庫" }));
    const reopened = await screen.findByLabelText("發票號碼");
    expect((reopened as HTMLInputElement).value).toBe("");
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

    // 建立採購單面板預設收合，先展開再建單。
    await user.click(await screen.findByRole("button", { name: "＋ 建立採購單" }));
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

  it("低庫存「補貨」把該品帶入建單草稿並展開面板", async () => {
    loginAs("CLERK");
    stubFetch((url) => {
      if (url.includes("/suppliers")) return json([SUPPLIER]);
      if (url.includes("/catalog-products") && url.includes("low_stock=true")) return json([CATALOG]);
      if (url.includes("/catalog-products")) return json([CATALOG]);
      if (url.includes("/purchase-orders")) return json([]);
      return null;
    });
    const user = userEvent.setup();
    renderPage();

    // 低庫存卡的「補貨」→ 面板展開、明細已含該品（進貨單價欄以品名標記）。
    await user.click(await screen.findByRole("button", { name: "補貨 瓦斯罐" }));
    expect(await screen.findByLabelText("進貨單價 瓦斯罐")).toBeTruthy();
    // 已在草稿中，供應商 combobox 亦已可見（面板展開）。
    expect(screen.getByLabelText("供應商")).toBeTruthy();
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

  it("點採購單單號開啟詳情，顯示明細品名與合計", async () => {
    loginAs("CLERK");
    stubFetch((url) => {
      if (url.includes("/suppliers")) return json([SUPPLIER]);
      if (url.includes("/catalog-products")) return json([CATALOG]);
      if (url.includes("/purchase-orders")) return json([ORDERED_PO]);
      return null;
    });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "#7" }));
    expect(await screen.findByText("採購單 #7")).toBeTruthy();
    // 明細以品名顯示（非 #42）；鎖定詳情表格儲存格（低庫存卡也有同名，故指定 td）。
    expect(screen.getByText("瓦斯罐", { selector: "td" })).toBeTruthy();
    expect(screen.getByText("合計")).toBeTruthy();
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
});
