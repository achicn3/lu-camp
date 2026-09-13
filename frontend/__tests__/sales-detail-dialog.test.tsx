// @vitest-environment jsdom
// 交易明細對話框（裁示 2026-09-12）：清單一列塞不下，點「明細」要一次看完
// 全部品項與經手資訊（收銀員、付款方式、會員、發票號碼）。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import SalesPage from "@/app/(authed)/sales/page";
import { clearToken, setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

const SUMMARY = {
  id: 42,
  store_id: 1,
  subtotal: "1300",
  tax: "62",
  total: "1300",
  invoice_status: "ISSUED",
  status: "COMPLETED",
  created_at: "2026-09-13T02:00:00Z",
  payment_method: "CASH",
  buyer_contact_id: 7,
  signature_task_id: null,
  service_mode: null,
  table_no: null,
  invoice_issue_channel: "AMEGO",
  invoice_print_mark: true,
  first_item_name: "USB 充電營燈",
  item_count: 2,
  invoice_no: "AB12345678",
};

// 明細另外帶回**當次查到**的發票號碼：清單那份可能是別台開立前的舊快照。
const DETAIL = {
  ...SUMMARY,
  invoice_no: "ZZ99887766",
  clerk_user_id: 3,
  clerk_name: "小美",
  buyer_name: "王小明",
  total_discount: "0",
  total_manual_discount: "0",
  gift_retail_value: "0",
  lines: [
    {
      id: 1,
      line_type: "CATALOG",
      serialized_item_id: null,
      catalog_product_id: 5,
      bulk_lot_id: null,
      menu_item_id: null,
      description: "USB 充電營燈",
      qty: 2,
      unit_price: "590",
      line_total: "1180",
      original_unit_price: null,
      discount_amount: "0",
      line_kind: "NORMAL",
      manual_discount_amount: "0",
      net_amount: "1180",
      gift_reason_name: null,
      gift_note: null,
      returned_qty: 0,
    },
    {
      id: 2,
      line_type: "CATALOG",
      serialized_item_id: null,
      catalog_product_id: 6,
      bulk_lot_id: null,
      menu_item_id: null,
      description: "營繩",
      qty: 1,
      unit_price: "120",
      line_total: "120",
      original_unit_price: "120",
      discount_amount: "0",
      line_kind: "GIFT",
      manual_discount_amount: "0",
      net_amount: "0",
      gift_reason_name: "滿額贈",
      gift_note: null,
      returned_qty: 0,
    },
  ],
  tenders: [{ id: 1, tender_type: "CASH", amount: "1180", fee_amount: "0" }],
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<SalesPage />, { wrapper });
}

afterEach(() => {
  cleanup();
  clearToken();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("交易紀錄的明細", () => {
  it("清單顯示交易內容與發票號碼，點明細看得到全部品項與經手資訊", async () => {
    setToken(fakeJwt({ sub: "1", role: "MANAGER", store_id: 1 }));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        const json = (data: unknown) =>
          new Response(JSON.stringify(data), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        if (/\/api\/v1\/sales\/\d+$/.test(url.pathname)) return json(DETAIL);
        if (url.pathname === "/api/v1/sales") return json([SUMMARY]);
        return json([]);
      }),
    );
    renderPage();

    // 清單：第一項品名＋共幾項、發票號碼都看得到，不必點開
    const row = await screen.findByRole("row", { name: /USB 充電營燈/ });
    expect(within(row).getByText(/等 2 項/)).toBeDefined();
    expect(within(row).getByText("AB12345678")).toBeDefined();

    await userEvent.click(within(row).getByRole("button", { name: /查看銷售 42 的明細/ }));

    const dialog = await screen.findByRole("dialog", { name: "交易明細" });
    expect(within(dialog).getByText("小美")).toBeDefined(); // 收銀員
    expect(within(dialog).getByText("王小明")).toBeDefined(); // 會員
    expect(within(dialog).getByText("現金")).toBeDefined(); // 付款方式
    // 全部品項（含贈品，並標示出來——它不計入應付，看明細的人必須分得出來）
    expect(within(dialog).getByText("USB 充電營燈")).toBeDefined();
    expect(within(dialog).getByText("營繩")).toBeDefined();
    expect(within(dialog).getByText("贈品")).toBeDefined();
    // 號碼取明細當次查到的那個，不是清單的舊快照
    expect(within(dialog).getByText("ZZ99887766")).toBeDefined();
    expect(within(dialog).queryByText("AB12345678")).toBeNull();
  });

  it("開啟時鍵盤焦點進入對話框且出不去，關閉後回到原本的按鈕", async () => {
    // 只用 aria-modal 擋不住鍵盤：焦點留在背景時按 Tab 會走到「退貨」，
    // Enter 就在這個唯讀視窗背後開了退貨流程（Codex 審查實測）。
    setToken(fakeJwt({ sub: "1", role: "MANAGER", store_id: 1 }));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        const json = (data: unknown) =>
          new Response(JSON.stringify(data), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        if (/\/api\/v1\/sales\/\d+$/.test(url.pathname)) return json(DETAIL);
        if (url.pathname === "/api/v1/sales") return json([SUMMARY]);
        return json([]);
      }),
    );
    renderPage();

    const row = await screen.findByRole("row", { name: /USB 充電營燈/ });
    const opener = within(row).getByRole("button", { name: /查看銷售 42 的明細/ });
    await userEvent.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "交易明細" });

    expect(dialog.contains(document.activeElement)).toBe(true);
    for (let i = 0; i < 6; i += 1) {
      await userEvent.tab();
      expect(dialog.contains(document.activeElement)).toBe(true);
    }

    // 品項區要在焦點循環裡：品項一多會出現捲軸，只用鍵盤的人得聚焦到它才捲得動。
    const lines = within(dialog).getByRole("group", { name: "交易品項（可捲動）" });
    expect(lines.tabIndex).toBe(0);
    let reachedLines = false;
    for (let i = 0; i < 6; i += 1) {
      await userEvent.tab();
      if (document.activeElement === lines) reachedLines = true;
    }
    expect(reachedLines).toBe(true);

    await userEvent.click(within(dialog).getByRole("button", { name: "關閉" }));
    expect(screen.queryByRole("dialog", { name: "交易明細" })).toBeNull();
    expect(document.activeElement).toBe(opener);
  });
});
