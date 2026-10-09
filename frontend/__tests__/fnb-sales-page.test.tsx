// @vitest-environment jsdom
// /fnb-sales 餐飲交易紀錄（docs/47）：只列含餐點的交易；退款只能退餐點，可勾「這份還能賣」；
// 混合單的二手商品只列出、引導到交易紀錄退貨。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import FnbSalesPage from "@/app/(authed)/fnb-sales/page";
import { setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Route = (url: string, method: string, body: unknown) => Response | null;

function stubFetch(route: Route) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      let body: unknown = null;
      if (input instanceof Request) body = await input.clone().json().catch(() => null);
      else if (init?.body) body = JSON.parse(String(init.body));
      const resp = route(url, method, body);
      if (resp) return resp;
      throw new Error(`unmatched fetch: ${method} ${url}`);
    }),
  );
}

function renderPage() {
  setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<FnbSalesPage />, { wrapper: Wrapper });
}

const ROW = {
  id: 12,
  created_at: "2026-10-01T03:30:00Z",
  status: "COMPLETED",
  service_mode: "DINE_IN",
  table_no: "A1",
  payment_method: "CASH",
  invoice_status: "NOT_ISSUED",
  buyer_contact_id: null,
  total: "890",
  food_items: "拿鐵（冰）×2、戚風×1",
  food_subtotal: "390",
  has_other_items: true,
  food_refunded: "0",
  total_refunded: "0",
};

const DETAIL = {
  id: 12,
  store_id: 1,
  subtotal: "848",
  tax: "42",
  total: "890",
  invoice_status: "NOT_ISSUED",
  status: "COMPLETED",
  created_at: "2026-10-01T03:30:00Z",
  payment_method: "CASH",
  buyer_contact_id: null,
  clerk_user_id: 1,
  awarded_points: 0,
  signature_task_id: null,
  lines: [
    {
      id: 1,
      line_type: "MENU",
      description: "拿鐵（冰）",
      qty: 2,
      returned_qty: 0,
      unit_price: "150",
      line_total: "300",
      net_amount: "300",
      manual_discount_amount: "0",
      line_kind: "NORMAL",
    },
    {
      id: 2,
      line_type: "CATALOG",
      description: "營燈",
      qty: 1,
      returned_qty: 0,
      unit_price: "500",
      line_total: "500",
      net_amount: "500",
      manual_discount_amount: "0",
      line_kind: "NORMAL",
    },
  ],
  tenders: [{ id: 9, tender_type: "CASH", amount: "890", fee_amount: "0" }],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("/fnb-sales 餐飲交易紀錄", () => {
  it("列出含餐點的交易：桌號、餐點摘要、餐點小計，混合單標明含二手商品", async () => {
    stubFetch((url) => (url.includes("/api/v1/sales/fnb") ? json([ROW]) : null));
    renderPage();
    const row = (await screen.findByText("拿鐵（冰）×2、戚風×1")).closest("tr")!;
    expect(within(row).getByText(/A1/)).toBeTruthy();
    expect(within(row).getByText(/390/)).toBeTruthy();
    expect(within(row).getByText("含二手商品")).toBeTruthy();
  });

  it("退款只列餐點、可勾還能賣；送出帶 resellable", async () => {
    let posted: unknown = null;
    stubFetch((url, method, body) => {
      if (url.includes("/api/v1/sales/fnb")) return json([ROW]);
      if (url.endsWith("/api/v1/sales/12") && method === "GET") return json(DETAIL);
      if (url.includes("/api/v1/returns/preview")) {
        return json({
          is_full_return: false,
          invoice_action: "NONE",
          manual_paper_resolvable: false,
          requires_paper_recall: false,
          requires_customer_consent: false,
          reason: "原交易沒有已開立的發票，本次退貨不涉及發票處置。",
          refund_total: "150",
          unreturned_gifts: [],
          refund_tenders: [{ tender_type: "CASH", amount: "150" }],
          refund_supported: true,
        });
      }
      if (url.match(/\/api\/v1\/returns$/) && method === "POST") {
        posted = body;
        return json({
          id: 5,
          store_id: 1,
          sale_id: 12,
          refund_amount: "150",
          reason: "太甜",
          clerk_user_id: 1,
          created_at: "2026-10-01T04:00:00Z",
          lines: [],
          refund_tenders: [{ id: 1, tender_type: "CASH", amount: "150" }],
        });
      }
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "餐點退款 12" }));
    const dialog = await screen.findByRole("dialog", { name: "餐點退款" });
    expect(within(dialog).queryByLabelText("營燈 退貨數量")).toBeNull(); // 二手不在這裡退
    // 2026-10-09 起餐點也能用購物金：說明不再寫「餐點不能用購物金付」（ADR-031）
    expect(dialog.textContent).not.toMatch(/不能用購物金/);
    expect(dialog.textContent).toMatch(/餐點退款退回原本的現金、LINE Pay 或台灣Pay/);
    const qty = within(dialog).getByLabelText("拿鐵（冰） 退貨數量");
    await user.clear(qty);
    await user.type(qty, "1");
    await user.click(within(dialog).getByLabelText("拿鐵（冰） 這份還能賣"));
    await user.type(within(dialog).getByLabelText("退貨原因"), "太甜");
    const preview = await within(dialog).findByLabelText("預估退款去向");
    expect(preview.textContent).toMatch(/現金.*150/);
    await user.click(within(dialog).getByRole("button", { name: "確認退款 $150" }));
    await waitFor(() => expect(posted).not.toBeNull());
    expect((posted as { lines: unknown }).lines).toEqual([
      { sale_line_id: 1, qty: 1, resellable: true },
    ]);
  });

  it("用購物金付的餐點：說明購物金那部分先退回購物金（ADR-031）", async () => {
    stubFetch((url, method) => {
      if (url.includes("/api/v1/sales/fnb")) return json([ROW]);
      if (url.endsWith("/api/v1/sales/12") && method === "GET") {
        return json({
          ...DETAIL,
          payment_method: "MIXED",
          tenders: [
            { id: 9, tender_type: "STORE_CREDIT", amount: "600", fee_amount: "0" },
            { id: 10, tender_type: "CASH", amount: "290", fee_amount: "0" },
          ],
        });
      }
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "餐點退款 12" }));
    const dialog = await screen.findByRole("dialog", { name: "餐點退款" });
    await waitFor(() => expect(dialog.textContent).toMatch(/購物金付的部分會先退回購物金/));
    expect(dialog.textContent).not.toMatch(/不能用購物金/);
  });

  it("餐點都退完的交易不能再按退款", async () => {
    stubFetch((url) =>
      url.includes("/api/v1/sales/fnb")
        ? json([{ ...ROW, food_refunded: "390", total_refunded: "390" }])
        : null,
    );
    renderPage();
    const button = await screen.findByRole("button", { name: "餐點退款 12" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("已開發票的餐點退款：請客人點選同意（推送的任務帶 consent_mode TAP）", async () => {
    let pushed: unknown = null;
    stubFetch((url, method, body) => {
      if (url.includes("/api/v1/sales/fnb")) return json([ROW]);
      if (url.endsWith("/api/v1/sales/12") && method === "GET") return json(DETAIL);
      if (url.includes("/api/v1/returns/preview")) {
        return json({
          is_full_return: false,
          invoice_action: "ALLOWANCE",
          manual_paper_resolvable: false,
          requires_paper_recall: false,
          requires_customer_consent: true,
          reason: "部分退貨：原發票對未退商品仍有效，開立折讓單。",
          refund_total: "150",
          unreturned_gifts: [],
          refund_tenders: [{ tender_type: "CASH", amount: "150" }],
          refund_supported: true,
        });
      }
      if (url.includes("/api/v1/customer-display/terminals") && method === "POST") {
        return json({ id: 3, paired_kiosk: { id: 5, online: true } });
      }
      if (url.includes("/api/v1/signing/tasks") && method === "POST") {
        pushed = body;
        return json({ id: 99, status: "PENDING" });
      }
      if (url.includes("/api/v1/signing/tasks/99")) return json({ id: 99, status: "PENDING" });
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "餐點退款 12" }));
    const dialog = await screen.findByRole("dialog", { name: "餐點退款" });
    const qty = within(dialog).getByLabelText("拿鐵（冰） 退貨數量");
    await user.clear(qty);
    await user.type(qty, "1");
    expect(await within(dialog).findByText("請先請客人於顧客螢幕點選同意")).toBeTruthy();
    await user.click(within(dialog).getByRole("button", { name: "請客人於顧客螢幕點選同意" }));
    await waitFor(() => expect(pushed).not.toBeNull());
    expect((pushed as { consent_mode?: string }).consent_mode).toBe("TAP");
    expect(await within(dialog).findByText("已送出，等待客人同意…")).toBeTruthy();
  });
});
