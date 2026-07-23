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

  it("有綁定簽署的交易可一鍵開啟簽名，未綁定者不顯示入口", async () => {
    stubFetch((url, method) => {
      if (url.includes("/api/v1/signing/tasks/41/signature") && method === "GET") {
        return new Response(new Blob(["png"], { type: "image/png" }));
      }
      if (url.includes("/api/v1/signing/tasks/41") && method === "GET") {
        return json({
          id: 41,
          store_id: 1,
          kind: "STORE_CREDIT_USE",
          status: "SIGNED",
          contact_id: 9,
          content: { debit: "300", balance_after: "700" },
          agreement_version: null,
          chosen_payout: null,
          has_signature: true,
          signed_at: "2026-07-02T03:29:00Z",
          cancelled_at: null,
          ref_type: null,
          ref_id: null,
          created_at: "2026-07-02T03:28:00Z",
          bound_acquisition_id: null,
          bound_sale_id: 7,
          agreement_title: null,
          agreement_body: null,
          signer_name: "林測試",
        });
      }
      if (url.includes("/linepay-refunds/pending")) return json([]);
      if (url.includes("/api/v1/sales") && method === "GET") {
        return json([
          sale(7, { signature_task_id: 41 }),
          sale(8, { signature_task_id: null }),
        ]);
      }
      return null;
    });
    const user = userEvent.setup();
    renderPage("CLERK");

    await waitFor(() => expect(screen.getByText("#7")).toBeTruthy());
    expect(screen.queryByLabelText("查看銷售 8 簽名")).toBeNull();
    await user.click(screen.getByLabelText("查看銷售 7 簽名"));

    const dialog = await screen.findByRole("dialog", { name: "簽署證據" });
    expect(within(dialog).getByText(/購物金扣抵確認 #41/)).toBeTruthy();
    expect(within(dialog).getByText(/簽署人：林測試/)).toBeTruthy();
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

  it("混合付款退貨：可整筆選取、顯示購物金優先拆帳並確認台灣Pay差額", async () => {
    let returned = false;
    stubFetch((url, method) => {
      if (url.includes("/api/v1/returns") && method === "POST") {
        returned = true;
        return json({
          id: 31,
          store_id: 1,
          sale_id: 7,
          refund_amount: "200",
          reason: "尺寸不合",
          clerk_user_id: 1,
          created_at: "2026-07-23T04:00:00Z",
          lines: [],
          refund_tenders: [
            { id: 41, tender_type: "STORE_CREDIT", amount: "100" },
            { id: 42, tender_type: "TAIWAN_PAY", amount: "100" },
          ],
        });
      }
      if (url.endsWith("/api/v1/sales/7") && method === "GET") {
        return json({
          ...sale(7, { payment_method: "MIXED", buyer_contact_id: 9 }),
          clerk_user_id: 1,
          awarded_points: 4,
          signature_task_id: null,
          lines: [
            {
              id: 71,
              line_type: "CATALOG",
              description: "瓦斯罐",
              qty: 2,
              returned_qty: 1,
              unit_price: "200",
              line_total: "400",
            },
          ],
          tenders: [
            { id: 81, tender_type: "STORE_CREDIT", amount: "300", fee_amount: "0" },
            { id: 82, tender_type: "TAIWAN_PAY", amount: "100", fee_amount: "0" },
          ],
        });
      }
      if (url.includes("/linepay-refunds/pending")) return json([]);
      if (url.includes("/api/v1/sales") && method === "GET") {
        return json([sale(7, { payment_method: "MIXED", buyer_contact_id: 9 })]);
      }
      return null;
    });
    const user = userEvent.setup();
    renderPage("CLERK");
    await user.click(await screen.findByLabelText("退貨銷售 7"));
    const dialog = await screen.findByRole("dialog", { name: "退貨" });

    await user.click(within(dialog).getByRole("button", { name: "整筆退貨" }));
    expect((within(dialog).getByLabelText("瓦斯罐 退貨數量") as HTMLInputElement).value).toBe(
      "1",
    );
    const preview = within(dialog).getByLabelText("預估退款去向");
    expect(preview.textContent).toMatch(/購物金.*100/);
    expect(preview.textContent).toMatch(/台灣Pay.*100/);
    await user.type(within(dialog).getByLabelText("退貨原因"), "尺寸不合");
    const confirm = within(dialog).getByRole("button", { name: "確認退貨 $200" });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    await user.click(within(dialog).getByLabelText(/已於台灣Pay完成退款 100 元/));
    await user.click(confirm);

    await waitFor(() => expect(returned).toBe(true));
    expect(await screen.findByText(/購物金.*100.*台灣Pay.*100/)).toBeTruthy();
  });

  it("舊資料若含多種外部付款，退貨應安全阻擋", async () => {
    stubFetch((url, method) => {
      if (url.endsWith("/api/v1/sales/7") && method === "GET") {
        return json({
          ...sale(7, { payment_method: "MIXED" }),
          clerk_user_id: 1,
          awarded_points: 10,
          signature_task_id: null,
          lines: [
            {
              id: 71,
              line_type: "CATALOG",
              description: "露營椅",
              qty: 1,
              returned_qty: 0,
              unit_price: "800",
              line_total: "800",
            },
            {
              id: 72,
              line_type: "CATALOG",
              description: "營繩",
              qty: 1,
              returned_qty: 0,
              unit_price: "200",
              line_total: "200",
            },
          ],
          tenders: [
            { id: 81, tender_type: "CASH", amount: "300", fee_amount: "0" },
            { id: 82, tender_type: "LINE_PAY", amount: "700", fee_amount: "0" },
          ],
        });
      }
      if (url.includes("/linepay-refunds/pending")) return json([]);
      if (url.includes("/api/v1/sales") && method === "GET") {
        return json([sale(7, { payment_method: "MIXED" })]);
      }
      return null;
    });
    const user = userEvent.setup();
    renderPage("CLERK");
    await user.click(await screen.findByLabelText("退貨銷售 7"));
    const dialog = await screen.findByRole("dialog", { name: "退貨" });
    expect(
      within(dialog).getByText(
        /此單包含多種外部付款渠道，系統無法安全判定退款順序/,
      ),
    ).toBeTruthy();
    expect(within(dialog).queryByLabelText("預估退款去向")).toBeNull();
    expect(
      (within(dialog).getByRole("button", { name: /確認退貨/ }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("購物金＋台灣Pay整筆作廢：仍須顯示手動退款確認", async () => {
    stubFetch((url, method) => {
      if (url.endsWith("/api/v1/sales/7") && method === "GET") {
        return json({
          ...sale(7, { payment_method: "MIXED", buyer_contact_id: 9 }),
          lines: [],
          tenders: [
            { id: 81, tender_type: "STORE_CREDIT", amount: "300", fee_amount: "0" },
            { id: 82, tender_type: "TAIWAN_PAY", amount: "700", fee_amount: "0" },
          ],
        });
      }
      if (url.includes("/linepay-refunds/pending")) return json([]);
      if (url.includes("/api/v1/sales") && method === "GET") {
        return json([sale(7, { payment_method: "MIXED", buyer_contact_id: 9 })]);
      }
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    await user.click(await screen.findByLabelText("作廢銷售 7"));
    const dialog = await screen.findByRole("dialog", { name: "作廢銷售確認" });

    expect(await within(dialog).findByLabelText(/已於台灣Pay App 完成退款/)).toBeTruthy();
    expect(
      (within(dialog).getByRole("button", { name: "確認作廢" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("購物金＋LINE Pay整筆作廢：說明購物金回補與LINE Pay自動退款", async () => {
    stubFetch((url, method) => {
      if (url.endsWith("/api/v1/sales/7") && method === "GET") {
        return json({
          ...sale(7, { payment_method: "MIXED", buyer_contact_id: 9 }),
          lines: [],
          tenders: [
            { id: 81, tender_type: "STORE_CREDIT", amount: "300", fee_amount: "0" },
            { id: 82, tender_type: "LINE_PAY", amount: "700", fee_amount: "0" },
          ],
        });
      }
      if (url.includes("/linepay-refunds/pending")) return json([]);
      if (url.includes("/api/v1/sales") && method === "GET") {
        return json([sale(7, { payment_method: "MIXED", buyer_contact_id: 9 })]);
      }
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    await user.click(await screen.findByLabelText("作廢銷售 7"));
    const dialog = await screen.findByRole("dialog", { name: "作廢銷售確認" });

    expect(
      await within(dialog).findByText(/購物金將回補原會員餘額.*LINE Pay.*自動原路退款/),
    ).toBeTruthy();
    expect(within(dialog).queryByText(/現金退還請直接自錢櫃取出/)).toBeNull();
  });
});
