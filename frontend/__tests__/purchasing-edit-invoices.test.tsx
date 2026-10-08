// @vitest-environment jsdom
// 採購單事後修改＋進項發票獨立登錄（docs/70）：修改入口權限、已收欄與送出內容、品名改字、
// 收貨批次的發票連結、登錄／修改／刪除發票。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const nav = vi.hoisted(() => ({ push: vi.fn(), search: "", id: "7" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: nav.push }),
  useSearchParams: () => new URLSearchParams(nav.search),
  useParams: () => ({ id: nav.id }),
}));

import EditPurchaseOrderPage from "@/app/(authed)/purchasing/[id]/edit/page";
import PurchaseOrderPage from "@/app/(authed)/purchasing/[id]/page";
import InputInvoicePage from "@/app/(authed)/purchasing/invoices/[id]/page";
import NewInputInvoicePage from "@/app/(authed)/purchasing/invoices/new/page";
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

type Call = { method: string; path: string; body: unknown };
type Route = (method: string, path: string, body: unknown) => Response | null;

function stubFetch(route: Route): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(input instanceof Request ? input.url : String(input));
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      const text = input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
      const body = text ? (JSON.parse(text) as unknown) : null;
      calls.push({ method, path: url.pathname + url.search, body });
      const resp = route(method, url.pathname, body);
      if (resp) return resp;
      throw new Error(`unmatched fetch: ${method} ${url.pathname}`);
    }),
  );
  return calls;
}

function renderWith(node: ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>);
}

const CATALOG = {
  id: 42,
  store_id: 1,
  sku: "GAS-001",
  name: "瓦斯灌",
  brand_id: null,
  unit_price: "120",
  quantity_on_hand: 10,
  reorder_point: 5,
  incoming_qty: 0,
};

const PO = {
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
  receipts: [] as object[],
};

const RECEIVED_PO = {
  ...PO,
  status: "RECEIVED",
  received_at: "2026-06-21T01:00:00Z",
  lines: [{ ...PO.lines[0], received_qty: 10 }],
  receipts: [{ id: 31, received_at: "2026-06-21T01:00:00Z", received_by: 1, invoice: null }],
};

const SUPPLIERS = [
  {
    id: 5,
    store_id: 1,
    name: "山林供應商",
    contact: null,
    tax_id: null,
    is_active: true,
    created_at: "2026-06-20T00:00:00Z",
    updated_at: "2026-06-20T00:00:00Z",
  },
];

const INVOICE = {
  id: 90,
  supplier_id: 5,
  supplier_name: "山林供應商",
  invoice_number: "AB12345678",
  invoice_date: "2026-07-31",
  invoice_total: "1050",
  invoice_net: "1000",
  invoice_tax: "50",
  created_at: "2026-08-01T00:00:00Z",
  receipts: [
    { receipt_id: 31, purchase_order_id: 7, received_at: "2026-06-21T01:00:00Z", amount: "600" },
  ],
  receipts_total: "600",
};

function baseRoutes(po: object, extra: Route = () => null): Route {
  return (method, path, body) => {
    const hit = extra(method, path, body);
    if (hit) return hit;
    if (path.endsWith("/catalog-products/filter-options")) return json({ brands: [] });
    if (path.endsWith("/catalog-products/42")) return json(CATALOG);
    if (path.endsWith("/settings")) return json({ tax_rate: "0.05" });
    if (path.endsWith("/purchase-orders/7") && method === "GET") return json(po);
    if (path.endsWith("/suppliers")) return json(SUPPLIERS);
    return null;
  };
}

afterEach(() => {
  cleanup();
  nav.search = "";
  nav.id = "7";
  clearToken();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  try {
    globalThis.localStorage?.clear();
  } catch {
    // jsdom 無 localStorage 時忽略
  }
});

describe("採購單明細的「修改」入口", () => {
  it("已下單：管理者看得到，店員看不到", async () => {
    loginAs("CLERK");
    stubFetch(baseRoutes(PO));
    renderWith(<PurchaseOrderPage />);
    await screen.findByText("採購單 #7");
    expect(screen.queryByRole("link", { name: "修改" })).toBeNull();
    cleanup();

    loginAs("MANAGER");
    renderWith(<PurchaseOrderPage />);
    const edit = await screen.findByRole("link", { name: "修改" });
    expect(edit.getAttribute("href")).toBe("/purchasing/7/edit");
  });

  it("草稿：店員也能改；取消的誰都不能改", async () => {
    loginAs("CLERK");
    stubFetch(baseRoutes({ ...PO, status: "DRAFT" }));
    renderWith(<PurchaseOrderPage />);
    expect(await screen.findByRole("link", { name: "修改" })).toBeTruthy();
    cleanup();

    loginAs("MANAGER");
    stubFetch(baseRoutes({ ...PO, status: "CANCELLED" }));
    renderWith(<PurchaseOrderPage />);
    await screen.findByText("採購單 #7");
    expect(screen.queryByRole("link", { name: "修改" })).toBeNull();
  });
});

describe("收貨批次的進項發票", () => {
  it("還沒開發票：顯示「尚未開發票」並可帶入這批去登錄", async () => {
    loginAs("CLERK");
    stubFetch(baseRoutes(RECEIVED_PO));
    renderWith(<PurchaseOrderPage />);
    await screen.findByText(/尚未開發票/);
    expect(screen.getByRole("link", { name: "登錄發票" }).getAttribute("href")).toBe(
      "/purchasing/invoices/new?supplier=5&receipt=31",
    );
  });

  it("已開發票：號碼連到那張發票", async () => {
    loginAs("CLERK");
    const withInvoice = {
      ...RECEIVED_PO,
      receipts: [{ ...RECEIVED_PO.receipts[0], invoice: { ...INVOICE, receipts: undefined } }],
    };
    stubFetch(baseRoutes(withInvoice));
    renderWith(<PurchaseOrderPage />);
    const link = await screen.findByRole("link", { name: "AB12345678" });
    expect(link.getAttribute("href")).toBe("/purchasing/invoices/90");
  });
});

describe("/purchasing/[id]/edit 修改採購單", () => {
  it("已收貨的單：帶入原內容與「已收」欄，改完送出整張（含原列 id 與已收）", async () => {
    loginAs("MANAGER");
    const calls = stubFetch(
      baseRoutes(RECEIVED_PO, (method, path) =>
        method === "PUT" && path.endsWith("/purchase-orders/7")
          ? json({ ...RECEIVED_PO, lines: [{ ...RECEIVED_PO.lines[0], qty: 2, received_qty: 2 }] })
          : null,
      ),
    );
    const user = userEvent.setup();
    renderWith(<EditPurchaseOrderPage />);

    const received = await screen.findByLabelText("已收 瓦斯灌");
    expect((received as HTMLInputElement).value).toBe("10");
    expect(screen.getByText(/庫存會跟著加減/)).toBeTruthy();
    await user.clear(screen.getByLabelText("數量 瓦斯灌"));
    await user.type(screen.getByLabelText("數量 瓦斯灌"), "2");
    await user.clear(received);
    await user.type(received, "2");
    await user.click(screen.getByRole("button", { name: "儲存修改" }));

    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/purchasing/7"));
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.body).toEqual({
      supplier_id: 5,
      lines: [{ id: 1, catalog_product_id: 42, qty: 2, received_qty: 2, unit_cost: "60" }],
    });
  });

  it("已收大於訂購就不能送出", async () => {
    loginAs("MANAGER");
    stubFetch(baseRoutes(RECEIVED_PO));
    const user = userEvent.setup();
    renderWith(<EditPurchaseOrderPage />);
    const received = await screen.findByLabelText("已收 瓦斯灌");
    await user.clear(received);
    await user.type(received, "11");
    expect(screen.getByRole("button", { name: "儲存修改" })).toHaveProperty("disabled", true);
  });

  it("店員開已下單的修改頁：說明只有管理者能改，不顯示表單", async () => {
    loginAs("CLERK");
    stubFetch(baseRoutes(PO));
    renderWith(<EditPurchaseOrderPage />);
    expect(await screen.findByText("已下單或已收貨的採購單只有管理者能修改。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "儲存修改" })).toBeNull();
  });

  it("管理者可直接改商品名稱的錯字", async () => {
    loginAs("MANAGER");
    const calls = stubFetch(
      baseRoutes(PO, (method, path) =>
        method === "PATCH" && path.endsWith("/catalog-products/42")
          ? json({ ...CATALOG, name: "瓦斯罐" })
          : null,
      ),
    );
    const user = userEvent.setup();
    renderWith(<EditPurchaseOrderPage />);
    await user.click(await screen.findByRole("button", { name: "改名 瓦斯灌" }));
    const input = screen.getByLabelText("新品名 瓦斯灌");
    await user.clear(input);
    await user.type(input, "瓦斯罐");
    await user.click(screen.getByRole("button", { name: "儲存品名" }));

    expect(await screen.findByRole("button", { name: "改名 瓦斯罐" })).toBeTruthy();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ name: "瓦斯罐" });
  });
});

describe("進項發票", () => {
  const UNINVOICED = [
    { receipt_id: 31, purchase_order_id: 7, received_at: "2026-06-21T01:00:00Z", amount: "600" },
    { receipt_id: 32, purchase_order_id: 8, received_at: "2026-06-25T01:00:00Z", amount: "400" },
  ];

  function invoiceRoutes(extra: Route = () => null): Route {
    return baseRoutes(PO, (method, path, body) => {
      const hit = extra(method, path, body);
      if (hit) return hit;
      if (path.endsWith("/suppliers/5/uninvoiced-receipts")) return json(UNINVOICED);
      if (path.endsWith("/purchase-input-invoices/90") && method === "GET") return json(INVOICE);
      return null;
    });
  }

  it("從收貨批次進來：供應商與那一批已帶好；合併勾兩批、照發票填，送出涵蓋的收貨", async () => {
    loginAs("CLERK");
    nav.search = "supplier=5&receipt=31";
    const calls = stubFetch(
      invoiceRoutes((method, path) =>
        method === "POST" && path.endsWith("/purchase-input-invoices")
          ? json({ ...INVOICE, id: 91 }, 201)
          : null,
      ),
    );
    const user = userEvent.setup();
    renderWith(<NewInputInvoicePage />);

    const first = await screen.findByLabelText(/採購單 #7 .* 收貨/);
    expect((first as HTMLInputElement).checked).toBe(true);
    await user.click(screen.getByLabelText(/採購單 #8 .* 收貨/));
    await user.type(screen.getByLabelText("發票號碼"), "ab12345678");
    await user.type(screen.getByLabelText("發票日期"), "2026-07-31");
    await user.type(screen.getByLabelText("發票未稅金額"), "1000");
    await user.type(screen.getByLabelText("發票稅額"), "50");
    await user.type(screen.getByLabelText("發票含稅金額"), "1050");
    // 勾選合計 1,000、發票 1,050：只提醒差額，不擋
    expect(screen.getByRole("status").textContent).toContain("差");
    await user.click(screen.getByRole("button", { name: "登錄發票" }));

    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/purchasing/invoices/91"));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      supplier_id: 5,
      receipt_ids: [31, 32],
      invoice_number: "AB12345678",
      invoice_date: "2026-07-31",
      invoice_net: "1000",
      invoice_tax: "50",
      invoice_total: "1050",
    });
  });

  it("未稅＋稅額對不上含稅：擋下並說明，不送出", async () => {
    loginAs("CLERK");
    nav.search = "supplier=5&receipt=31";
    const calls = stubFetch(invoiceRoutes());
    const user = userEvent.setup();
    renderWith(<NewInputInvoicePage />);
    await screen.findByLabelText(/採購單 #7 .* 收貨/);
    await user.type(screen.getByLabelText("發票號碼"), "AB12345678");
    await user.type(screen.getByLabelText("發票日期"), "2026-07-31");
    await user.type(screen.getByLabelText("發票未稅金額"), "1000");
    await user.type(screen.getByLabelText("發票稅額"), "40");
    await user.type(screen.getByLabelText("發票含稅金額"), "1050");
    await user.click(screen.getByRole("button", { name: "登錄發票" }));

    expect((await screen.findByRole("alert")).textContent).toContain("要等於含稅金額");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("管理者可修改：原本涵蓋的那批已勾、可再加一批，送出整張", async () => {
    loginAs("MANAGER");
    nav.id = "90";
    const calls = stubFetch(
      invoiceRoutes((method, path) =>
        method === "PUT" && path.endsWith("/purchase-input-invoices/90") ? json(INVOICE) : null,
      ),
    );
    const user = userEvent.setup();
    renderWith(<InputInvoicePage />);
    const original = await screen.findByLabelText(/採購單 #7 .* 收貨/);
    expect((original as HTMLInputElement).checked).toBe(true);
    await user.click(screen.getByLabelText(/採購單 #8 .* 收貨/));
    await user.click(screen.getByRole("button", { name: "儲存修改" }));

    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")?.body as { receipt_ids: number[] };
    expect(put.receipt_ids).toEqual([31, 32]);
  });

  it("管理者刪除要按兩次確認；刪完回清單", async () => {
    loginAs("MANAGER");
    nav.id = "90";
    const calls = stubFetch(
      invoiceRoutes((method, path) =>
        method === "DELETE" && path.endsWith("/purchase-input-invoices/90")
          ? new Response(null, { status: 204 })
          : null,
      ),
    );
    const user = userEvent.setup();
    renderWith(<InputInvoicePage />);
    await user.click(await screen.findByRole("button", { name: "刪除發票" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(screen.getByRole("button", { name: "確定刪除這張發票" }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/purchasing?tab=invoices"));
  });

  it("店員只能看，不能改", async () => {
    loginAs("CLERK");
    nav.id = "90";
    stubFetch(invoiceRoutes());
    renderWith(<InputInvoicePage />);
    expect(await screen.findByText("發票 AB12345678")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "儲存修改" })).toBeNull();
    expect(screen.getByText("登錄後要修改或刪除，請找管理者。")).toBeTruthy();
  });

  it("採購頁的「進項發票」分頁列出發票與涵蓋的採購單", async () => {
    loginAs("CLERK");
    nav.search = "tab=invoices";
    stubFetch(
      invoiceRoutes((_method, path) => {
        if (path.endsWith("/purchase-input-invoices/count")) return json({ count: 1 });
        if (path.endsWith("/purchase-input-invoices")) return json([INVOICE]);
        return null;
      }),
    );
    renderWith(<PurchasingPage />);
    const row = (await screen.findByRole("link", { name: "AB12345678" })).closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("#7")).toBeTruthy();
    expect(within(row as HTMLElement).getByText("1 批收貨")).toBeTruthy();
  });
});
