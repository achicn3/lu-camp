// @vitest-environment jsdom
// 退貨對發票的處置（作廢／折讓）在 /sales 退貨對話框的把關：
// 依後端預覽顯示會做什麼、需收回紙本時未勾選不得送出、需買受人同意時未簽名不得送出。
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
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SALE = {
  id: 7,
  store_id: 1,
  subtotal: "952",
  tax: "48",
  total: "1000",
  invoice_status: "ISSUED",
  status: "COMPLETED",
  payment_method: "CASH",
  buyer_contact_id: null,
  created_at: "2026-08-01T03:30:00Z",
};

const SALE_DETAIL = {
  ...SALE,
  clerk_user_id: 1,
  awarded_points: 0,
  signature_task_id: null,
  lines: [
    {
      id: 71,
      line_type: "CATALOG",
      description: "露營椅",
      qty: 1,
      returned_qty: 0,
      unit_price: "1000",
      line_total: "1000",
    },
  ],
  tenders: [{ id: 81, tender_type: "CASH", amount: "1000", fee_amount: "0" }],
};

interface Options {
  preview: Record<string, unknown>;
  /** 簽署任務被輪詢時回報的狀態序列（依序取用，最後一個會重複）。 */
  taskStatuses?: string[];
}

function stub(options: Options) {
  const calls: { returnBody: Record<string, unknown> | null; consentBody: unknown } = {
    returnBody: null,
    consentBody: null,
  };
  const statuses = [...(options.taskStatuses ?? [])];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      // openapi-fetch 以 Request 物件送出，body 不在 init 上——要從 Request 讀回，
      // 否則斷言會對著 null 通過（假綠）。
      let body: Record<string, unknown> | null = null;
      if (input instanceof Request) {
        body = (await input.clone().json().catch(() => null)) as Record<string, unknown> | null;
      } else if (init?.body) {
        body = JSON.parse(String(init.body)) as Record<string, unknown>;
      }
      if (url.includes("/api/v1/returns/preview") && method === "POST") {
        return json(options.preview);
      }
      if (url.includes("/api/v1/customer-display/terminals") && method === "POST") {
        return json({ id: 3, paired_kiosk: { id: 5, online: true } });
      }
      if (url.includes("/api/v1/signing/tasks") && method === "POST") {
        calls.consentBody = body;
        return json({ id: 99, status: "PENDING" });
      }
      if (url.includes("/api/v1/signing/tasks/99") && method === "GET") {
        const status = statuses.length > 1 ? statuses.shift() : statuses[0];
        return json({ id: 99, status: status ?? "PENDING" });
      }
      if (url.match(/\/api\/v1\/returns$/) && method === "POST") {
        calls.returnBody = body;
        return json({
          id: 31,
          store_id: 1,
          sale_id: 7,
          refund_amount: "1000",
          reason: "不合適",
          clerk_user_id: 1,
          created_at: "2026-08-01T04:00:00Z",
          lines: [],
          refund_tenders: [{ id: 41, tender_type: "CASH", amount: "1000" }],
        });
      }
      if (url.endsWith("/api/v1/sales/7") && method === "GET") return json(SALE_DETAIL);
      if (url.includes("/linepay-refunds/pending")) return json([]);
      if (url.includes("/api/v1/sales") && method === "GET") return json([SALE]);
      throw new Error(`unmatched fetch: ${method} ${url}`);
    }),
  );
  return calls;
}

function renderPage() {
  setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<SalesPage />, { wrapper: Wrapper });
}

const VOID_PREVIEW = {
  is_full_return: true,
  invoice_action: "VOID",
  requires_paper_recall: true,
  requires_customer_consent: true,
  reason: "整筆退貨且原發票為本月開立：作廢原發票。需先向客人收回紙本證明聯。",
};

async function openDialogWithFullReturn(user: ReturnType<typeof userEvent.setup>) {
  renderPage();
  await user.click(await screen.findByLabelText("退貨銷售 7"));
  const dialog = await screen.findByRole("dialog", { name: "退貨" });
  await user.click(within(dialog).getByRole("button", { name: "整筆退貨" }));
  await user.type(within(dialog).getByLabelText("退貨原因"), "不合適");
  return dialog;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("退貨對話框的發票處置把關", () => {
  it("同月整筆退貨：明白告知會作廢原發票，並要求收回紙本與客人簽名", async () => {
    stub({ preview: VOID_PREVIEW, taskStatuses: ["PENDING"] });
    const user = userEvent.setup();
    const dialog = await openDialogWithFullReturn(user);

    expect(await within(dialog).findByText("作廢原發票")).toBeTruthy();
    expect(within(dialog).getByText(/需先向客人收回紙本證明聯/)).toBeTruthy();
    await waitFor(() =>
      expect(within(dialog).getByText(/請先向客人收回發票證明聯（紙本）並勾選確認/)).toBeTruthy(),
    );
    expect(within(dialog).getByText(/請先請客人於顧客螢幕簽名同意/)).toBeTruthy();
    expect(
      (within(dialog).getByRole("button", { name: /確認退貨/ }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("僅勾選收回紙本仍不得送出——同意簽名是另一道要求", async () => {
    stub({ preview: VOID_PREVIEW, taskStatuses: ["PENDING"] });
    const user = userEvent.setup();
    const dialog = await openDialogWithFullReturn(user);

    await user.click(
      await within(dialog).findByLabelText("已向客人收回發票證明聯（紙本）"),
    );
    expect(
      within(dialog).queryByText(/請先向客人收回發票證明聯（紙本）並勾選確認/),
    ).toBeNull();
    expect(
      (within(dialog).getByRole("button", { name: /確認退貨/ }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("收回紙本＋客人簽名完成後可送出，且送出時帶上兩項證明", async () => {
    const calls = stub({ preview: VOID_PREVIEW, taskStatuses: ["SIGNED"] });
    const user = userEvent.setup();
    const dialog = await openDialogWithFullReturn(user);

    await user.click(
      await within(dialog).findByLabelText("已向客人收回發票證明聯（紙本）"),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "請客人於顧客螢幕簽名同意" }),
    );
    expect(await within(dialog).findByText(/客人已簽名同意/)).toBeTruthy();

    const confirm = within(dialog).getByRole("button", { name: /確認退貨/ });
    await waitFor(() => expect((confirm as HTMLButtonElement).disabled).toBe(false));
    await user.click(confirm);

    await waitFor(() => expect(calls.returnBody).not.toBeNull());
    expect(calls.returnBody?.invoice_recalled).toBe(true);
    expect(calls.returnBody?.consent_signature_task_id).toBe(99);
    // 同意任務只帶退貨範圍；金額與處置由後端重建（客端不敘述證據）。
    expect(calls.consentBody).toMatchObject({
      kind: "RETURN_INVOICE_CONSENT",
      ref_type: "sale",
      ref_id: 7,
      content: { lines: [{ sale_line_id: 71, qty: 1 }] },
    });
  });

  it("載具發票（無紙本）：不要求收回，只要簽名同意", async () => {
    stub({
      preview: {
        ...VOID_PREVIEW,
        requires_paper_recall: false,
        reason: "整筆退貨且原發票為本月開立：作廢原發票（客人使用載具或捐贈，無紙本須收回）。",
      },
      taskStatuses: ["SIGNED"],
    });
    const user = userEvent.setup();
    const dialog = await openDialogWithFullReturn(user);

    expect(await within(dialog).findByText(/無紙本須收回/)).toBeTruthy();
    expect(
      within(dialog).queryByLabelText("已向客人收回發票證明聯（紙本）"),
    ).toBeNull();
  });

  it("部分退貨走折讓：不要求收回紙本，但仍須客人同意", async () => {
    stub({
      preview: {
        is_full_return: false,
        invoice_action: "ALLOWANCE",
        requires_paper_recall: false,
        requires_customer_consent: true,
        reason: "部分退貨：原發票對未退商品仍有效，開立折讓單。",
      },
      taskStatuses: ["PENDING"],
    });
    const user = userEvent.setup();
    const dialog = await openDialogWithFullReturn(user);

    expect(await within(dialog).findByText("開立折讓單")).toBeTruthy();
    expect(within(dialog).queryByLabelText("已向客人收回發票證明聯（紙本）")).toBeNull();
    expect(within(dialog).getByText(/請先請客人於顧客螢幕簽名同意/)).toBeTruthy();
  });

  it("原發票狀態未收斂：一律擋下，要求人工處理", async () => {
    stub({
      preview: {
        is_full_return: true,
        invoice_action: "REVIEW_REQUIRED",
        requires_paper_recall: false,
        requires_customer_consent: true,
        reason: "原發票的作廢尚在處理中（結果未確認），不可再疊加稅務動作，請轉人工處理。",
      },
    });
    const user = userEvent.setup();
    const dialog = await openDialogWithFullReturn(user);

    expect(
      await within(dialog).findByText(/原發票狀態尚未確認，請待處理完成或聯繫管理者/),
    ).toBeTruthy();
    expect(
      (within(dialog).getByRole("button", { name: /確認退貨/ }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("沒有發票的交易：不出現任何發票處置提示，維持原本的退貨流程", async () => {
    const calls = stub({
      preview: {
        is_full_return: true,
        invoice_action: "NONE",
        requires_paper_recall: false,
        requires_customer_consent: false,
        reason: "原交易沒有已開立的發票，本次退貨不涉及發票處置。",
      },
    });
    const user = userEvent.setup();
    const dialog = await openDialogWithFullReturn(user);

    expect(within(dialog).queryByLabelText("發票處置")).toBeNull();
    const confirm = within(dialog).getByRole("button", { name: /確認退貨/ });
    await waitFor(() => expect((confirm as HTMLButtonElement).disabled).toBe(false));
    await user.click(confirm);
    await waitFor(() => expect(calls.returnBody).not.toBeNull());
    expect(calls.returnBody?.consent_signature_task_id).toBeNull();
  });
});
