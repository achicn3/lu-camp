// @vitest-environment jsdom
// /sales 交易紀錄頁：當日列表、店長作廢（二次確認）、已作廢/已退貨停用、店員無作廢鈕。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import SalesPage from "@/app/(authed)/sales/page";
import { setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) =>
    Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type FetchRoute = (url: string, method: string) => Response | null;

function stubFetch(route: FetchRoute) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method =
        (input instanceof Request ? input.method : init?.method) ?? "GET";
      const resp = route(url, method);
      if (resp) return resp;
      throw new Error(`unmatched fetch: ${method} ${url}`);
    }),
  );
}

function renderPage(role: "MANAGER" | "CLERK") {
  setToken(fakeJwt({ sub: "1", role, store_id: 1 }));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<SalesPage />, { wrapper: Wrapper });
}

function sale(
  id: number,
  overrides: Partial<Record<string, unknown>> = {},
): Record<string, unknown> {
  return {
    id,
    store_id: 1,
    subtotal: "952",
    tax: "48",
    total: "1000",
    invoice_status: "NOT_ISSUED",
    status: "COMPLETED",
    created_at: "2026-07-02T03:30:00Z",
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("/sales 交易紀錄頁", () => {
  it("店長：列表渲染、作廢走二次確認、成功後刷新顯示已作廢", async () => {
    let voided = false;
    stubFetch((url, method) => {
      if (url.includes("/linepay-refunds/pending")) return json([]);
      if (url.includes("/api/v1/sales/7/void") && method === "POST") {
        voided = true;
        return json(sale(7, { invoice_status: "VOID" }));
      }
      if (url.includes("/api/v1/sales") && method === "GET") {
        return json([
          sale(7),
          sale(6, { invoice_status: "VOID" }), // 已作廢：不再有作廢鈕
        ]);
      }
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");

    await waitFor(() => expect(screen.getByText("#7")).toBeTruthy());
    expect(screen.getByText("#6")).toBeTruthy();
    // 已作廢列顯示標籤、無作廢鈕；可作廢列有鈕。
    expect(screen.getAllByText("已作廢").length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("作廢銷售 6")).toBeNull();

    await user.click(screen.getByLabelText("作廢銷售 7"));
    const dialog = await screen.findByRole("dialog", { name: "作廢銷售確認" });
    expect(within(dialog).getByText(/作廢銷售 #7/)).toBeTruthy();
    await user.click(within(dialog).getByRole("button", { name: "確認作廢" }));

    await waitFor(() => expect(voided).toBe(true));
    await waitFor(() =>
      expect(screen.getByText(/銷售 #7 已作廢/)).toBeTruthy(),
    );
  });

  it("店長：作廢被後端拒（409）→ 對話框顯示錯誤、不誤報成功", async () => {
    stubFetch((url, method) => {
      if (url.includes("/linepay-refunds/pending")) return json([]);
      if (url.includes("/void") && method === "POST") {
        return json({ detail: "sale 7 已有退貨，不可作廢" }, 409);
      }
      if (url.includes("/api/v1/sales") && method === "GET") {
        return json([sale(7)]);
      }
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    await waitFor(() => expect(screen.getByText("#7")).toBeTruthy());
    await user.click(screen.getByLabelText("作廢銷售 7"));
    const dialog = await screen.findByRole("dialog", { name: "作廢銷售確認" });
    await user.click(within(dialog).getByRole("button", { name: "確認作廢" }));
    await waitFor(() =>
      expect(within(dialog).getByText(/已有退貨，不可作廢/)).toBeTruthy(),
    );
    expect(screen.queryByText(/已作廢。/)).toBeNull();
  });

  it("店員：看得到列表、沒有作廢鈕", async () => {
    stubFetch((url, method) => {
      if (url.includes("/linepay-refunds/pending")) return json([]);
      if (url.includes("/api/v1/sales") && method === "GET") {
        return json([sale(7)]);
      }
      return null;
    });
    renderPage("CLERK");
    await waitFor(() => expect(screen.getByText("#7")).toBeTruthy());
    expect(screen.queryByLabelText("作廢銷售 7")).toBeNull();
  });

  it("已退貨的單：無作廢鈕（請走退貨流程）", async () => {
    stubFetch((url, method) => {
      if (url.includes("/linepay-refunds/pending")) return json([]);
      if (url.includes("/api/v1/sales") && method === "GET") {
        return json([sale(8, { status: "RETURNED" })]);
      }
      return null;
    });
    renderPage("MANAGER");
    await waitFor(() => expect(screen.getByText("#8")).toBeTruthy());
    expect(screen.getByText("已退貨")).toBeTruthy();
    expect(screen.queryByLabelText("作廢銷售 8")).toBeNull();
  });
});
