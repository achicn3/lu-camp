// @vitest-environment jsdom
// 採購單日期與付款條件的畫面（docs/99 §5、§7，行事曆 1B）：誰能改什麼、送出內容。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const nav = vi.hoisted(() => ({ id: "7" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
  useParams: () => ({ id: nav.id }),
}));

import PurchaseOrderPage from "@/app/(authed)/purchasing/[id]/page";
import { SupplierManager } from "@/features/purchasing/SupplierManager";
import { clearToken, setToken } from "@/lib/token";

let role: "MANAGER" | "CLERK" = "CLERK";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function loginAs(next: "MANAGER" | "CLERK") {
  role = next;
  setToken(fakeJwt({ sub: "1", role: next, store_id: 1 }));
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
      calls.push({ method, path: url.pathname, body });
      if (url.pathname.endsWith("/auth/me")) return json({ id: 1, role, store_id: 1 });
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
  ordered_on: "2026-06-20",
  expected_on: "2026-06-25",
  payment_terms: "MONTHLY",
  payment_terms_value: 10,
  payment_due_on: "2026-07-10",
  payment_due_overridden: true,
};

const CATALOG = {
  id: 42,
  store_id: 1,
  sku: "GAS-001",
  name: "瓦斯罐",
  brand_id: null,
  unit_price: "120",
  quantity_on_hand: 10,
  reorder_point: 5,
  incoming_qty: 0,
};

function poRoutes(po: object, extra: Route = () => null): Route {
  return (method, path, body) => {
    const hit = extra(method, path, body);
    if (hit) return hit;
    if (path.endsWith("/catalog-products/filter-options")) return json({ brands: [] });
    if (path.endsWith("/catalog-products/42")) return json(CATALOG);
    if (path.endsWith("/purchase-orders/7") && method === "GET") return json(po);
    return null;
  };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-06-26T02:00:00Z"));
});

afterEach(() => {
  cleanup();
  clearToken();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("採購單的日期與付款", () => {
  it("店員：看得到下單日、可以改預計到貨日；看不到付款期限", async () => {
    loginAs("CLERK");
    const calls = stubFetch(
      poRoutes(PO, (method, path) =>
        method === "PATCH" && path.endsWith("/purchase-orders/7/schedule")
          ? json({ ...PO, expected_on: "2026-06-28" })
          : null,
      ),
    );
    renderWith(<PurchaseOrderPage />);
    await screen.findByText("採購單 #7");

    expect(screen.getByText("2026/06/20")).toBeTruthy();
    expect(screen.getByText("（逾期未到）")).toBeTruthy();
    expect(screen.getByText("月結（每月 10 號付上個月的）")).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("付款期限")).toBeNull());

    const user = userEvent.setup();
    const expectedRow = screen.getByText("預計到貨日").closest("div") as HTMLElement;
    await user.click(within(expectedRow).getByRole("button", { name: "改" }));
    const input = within(expectedRow).getByLabelText("預計到貨日") as HTMLInputElement;
    await user.clear(input);
    await user.type(input, "2026-06-28");
    await user.click(within(expectedRow).getByRole("button", { name: "儲存" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ expected_on: "2026-06-28" });
  });

  it("管理者：看得到付款期限（手動改過會標示），可以恢復自動", async () => {
    loginAs("MANAGER");
    const calls = stubFetch(
      poRoutes(PO, (method, path) =>
        method === "PATCH" && path.endsWith("/purchase-orders/7/schedule")
          ? json({ ...PO, payment_due_on: null, payment_due_overridden: false })
          : null,
      ),
    );
    renderWith(<PurchaseOrderPage />);
    expect(await screen.findByText("2026/07/10（手動）")).toBeTruthy();

    await userEvent.setup().click(screen.getByRole("button", { name: "恢復自動" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ payment_due_on: null });
  });

  it("草稿送出時帶實際下單日（可以選前一天補登）", async () => {
    loginAs("CLERK");
    const draft = {
      ...PO,
      status: "DRAFT",
      ordered_on: null,
      payment_terms: null,
      payment_terms_value: null,
      payment_due_on: null,
      payment_due_overridden: false,
    };
    const calls = stubFetch(
      poRoutes(draft, (method, path) =>
        method === "POST" && path.endsWith("/purchase-orders/7/submit")
          ? json({ ...PO, ordered_on: "2026-06-25" })
          : null,
      ),
    );
    renderWith(<PurchaseOrderPage />);
    await screen.findByText("採購單 #7");
    expect(screen.getByText("送出時決定")).toBeTruthy();

    const user = userEvent.setup();
    const day = screen.getByLabelText("實際下單日") as HTMLInputElement;
    expect(day.value).toBe("2026-06-26");
    await user.clear(day);
    await user.type(day, "2026-06-25");
    await user.click(screen.getByRole("button", { name: "送出採購" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/submit"))).toBe(true));
    expect(calls.find((c) => c.path.endsWith("/submit"))?.body).toEqual({ ordered_on: "2026-06-25" });
  });
});

describe("供應商的付款條件", () => {
  const emptyList: Route = (method, path) => {
    if (path.endsWith("/suppliers/count")) return json({ count: 0 });
    if (path.endsWith("/suppliers") && method === "GET") return json([]);
    return null;
  };

  it("管理者新增供應商時可以設月結幾號", async () => {
    loginAs("MANAGER");
    const calls = stubFetch((method, path, body) =>
      method === "POST" && path.endsWith("/suppliers")
        ? json({ id: 9, name: "露營批發", ...(body as object) }, 201)
        : emptyList(method, path, body),
    );
    renderWith(<SupplierManager />);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("供應商名稱"), "露營批發");
    await user.selectOptions(await screen.findByLabelText("付款條件"), "MONTHLY");
    await user.type(screen.getByLabelText("每月幾號付"), "10");
    await user.click(screen.getByRole("button", { name: "新增供應商" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({
      name: "露營批發",
      payment_terms: "MONTHLY",
      payment_terms_value: 10,
    });
  });

  it("店員看不到付款條件的欄位，新增時也不送", async () => {
    loginAs("CLERK");
    const calls = stubFetch((method, path, body) =>
      method === "POST" && path.endsWith("/suppliers")
        ? json({ id: 9, name: "店員建", ...(body as object) }, 201)
        : emptyList(method, path, body),
    );
    renderWith(<SupplierManager />);
    const user = userEvent.setup();
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/auth/me"))).toBe(true));
    expect(screen.queryByLabelText("付款條件")).toBeNull();
    await user.type(screen.getByLabelText("供應商名稱"), "店員建");
    await user.click(screen.getByRole("button", { name: "新增供應商" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).not.toHaveProperty("payment_terms");
  });
});
