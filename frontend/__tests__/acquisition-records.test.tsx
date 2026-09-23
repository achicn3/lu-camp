// @vitest-environment jsdom
// 收購紀錄清單（2026-09-23 裁示）：店員也能看，作廢鈕限管理者；不能作廢的單事先標原因、
// 按鈕反灰（原因由後端 void_block 算好，前端只負責講清楚）。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ role: "MANAGER" as "MANAGER" | "CLERK" }));
vi.mock("@/lib/auth", () => ({
  decodeSession: () => ({ userId: 1, role: auth.role, storeId: 1 }),
  logout: vi.fn(),
}));

import { AcquisitionRecords } from "@/features/acquisition/AcquisitionRecords";

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function row(over: Record<string, unknown> = {}) {
  return {
    id: 12,
    created_at: "2026-09-20T03:00:00Z",
    type: "BUYOUT",
    contact_id: 7,
    seller_name: "林賣家",
    clerk_name: "阿明",
    item_count: 4,
    item_names: ["營燈", "睡袋", "爐頭"],
    payout_method: "CASH",
    total_cash_paid: "4000",
    payout_cash_amount: "4000",
    payout_credit_cash_equivalent: null,
    voided_at: null,
    void_block: null,
    ...over,
  };
}

const ROWS = [
  row(),
  row({ id: 11, type: "CONSIGNMENT", void_block: "CONSIGNMENT", item_count: 1, item_names: ["寄賣椅"], payout_cash_amount: null, total_cash_paid: null }),
  row({ id: 10, void_block: "HAS_SOLD_ITEMS" }),
  row({ id: 9, void_block: "CREDIT_SPENT", payout_method: "STORE_CREDIT", payout_cash_amount: null, payout_credit_cash_equivalent: "1100" }),
  row({ id: 8, void_block: "NO_OPEN_CASH_SESSION" }),
  row({ id: 7, void_block: "ALREADY_VOIDED", voided_at: "2026-09-21T03:00:00Z" }),
];

let requests: { url: string; method: string }[] = [];

function stub({ total = ROWS.length, items = ROWS }: { total?: number; items?: unknown[] } = {}) {
  requests = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : null;
      const url = request?.url ?? String(input);
      const method = request?.method ?? "GET";
      requests.push({ url, method });
      if (url.includes("/void")) {
        return json({
          acquisition_id: 12,
          voided_at: "2026-09-23T03:00:00Z",
          reversed_cash: "4000",
          reversed_credit: "0",
        });
      }
      return json({ total, items });
    }),
  );
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

function rowOf(id: number): HTMLElement {
  const cell = screen.getByText(`#${id}`);
  const tr = cell.closest("tr");
  if (!tr) throw new Error(`找不到 #${id}`);
  return tr;
}

const listCalls = () => requests.filter((r) => r.method === "GET" && /\/acquisitions\?|\/acquisitions$/.test(r.url));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("收購紀錄清單", () => {
  it("每列看得到時間、賣方、類型、品項、撥款與經手人", async () => {
    auth.role = "CLERK";
    stub();
    wrap(<AcquisitionRecords />);

    const first = await waitFor(() => rowOf(12));
    expect(first.textContent).toContain("林賣家");
    expect(first.textContent).toContain("買斷");
    expect(first.textContent).toContain("營燈、睡袋、爐頭 等 4 件");
    expect(first.textContent).toContain("現金 4,000");
    expect(first.textContent).toContain("阿明");
    expect(rowOf(9).textContent).toContain("購物金 1,100");
    expect(rowOf(7).textContent).toContain("已作廢");
  });

  it("店員看得到清單，但沒有作廢鈕", async () => {
    auth.role = "CLERK";
    stub();
    wrap(<AcquisitionRecords />);
    await waitFor(() => rowOf(12));
    expect(screen.queryByRole("button", { name: "作廢" })).toBeNull();
  });

  it("管理者：可作廢的單有作廢鈕；不能作廢的單反灰並講原因", async () => {
    auth.role = "MANAGER";
    stub();
    wrap(<AcquisitionRecords />);
    await waitFor(() => rowOf(12));

    const ok = within(rowOf(12)).getByRole("button", { name: "作廢" }) as HTMLButtonElement;
    expect(ok.disabled).toBe(false);
    const expectations: [number, string][] = [
      [11, "寄售"],
      [10, "已有商品賣出"],
      [9, "購物金已被用掉"],
      [8, "先開帳"],
      [7, "已作廢"],
    ];
    for (const [id, reason] of expectations) {
      const r = rowOf(id);
      const btn = within(r).getByRole("button", { name: "作廢" }) as HTMLButtonElement;
      expect(btn.disabled).toBe(true);
      expect(r.textContent).toContain(reason);
    }
  });

  it("按作廢 → 填原因確認 → 顯示結果並重新整理清單", async () => {
    auth.role = "MANAGER";
    stub();
    const user = userEvent.setup();
    wrap(<AcquisitionRecords />);
    await waitFor(() => rowOf(12));
    const before = listCalls().length;

    await user.click(within(rowOf(12)).getByRole("button", { name: "作廢" }));
    const dialog = await screen.findByRole("dialog", { name: "作廢收購確認" });
    await user.type(within(dialog).getByLabelText("作廢原因"), "登錄錯誤");
    await user.click(within(dialog).getByRole("button", { name: "確認作廢" }));

    expect(await screen.findByText(/已作廢收購單 #12/)).toBeTruthy();
    expect(requests.some((r) => r.method === "POST" && r.url.includes("/acquisitions/12/void"))).toBe(true);
    await waitFor(() => expect(listCalls().length).toBeGreaterThan(before));
  });

  it("篩選：類型、狀態、賣方搜尋都會帶到查詢", async () => {
    auth.role = "MANAGER";
    stub();
    const user = userEvent.setup();
    wrap(<AcquisitionRecords />);
    await waitFor(() => rowOf(12));

    await user.click(screen.getByRole("button", { name: "寄售" }));
    await waitFor(() => expect(listCalls().at(-1)?.url).toContain("type=CONSIGNMENT"));
    await user.click(screen.getByRole("button", { name: "只看已作廢" }));
    await waitFor(() => expect(listCalls().at(-1)?.url).toContain("voided=true"));
    await user.type(screen.getByLabelText("賣方搜尋"), "林");
    await user.click(screen.getByRole("button", { name: "搜尋" }));
    await waitFor(() => expect(decodeURIComponent(listCalls().at(-1)?.url ?? "")).toContain("q=林"));
  });

  it("翻頁：第二頁從第 51 筆開始", async () => {
    auth.role = "MANAGER";
    stub({ total: 120 });
    const user = userEvent.setup();
    wrap(<AcquisitionRecords />);
    await waitFor(() => rowOf(12));
    await user.click(screen.getByRole("button", { name: /下一頁/ }));
    await waitFor(() => expect(listCalls().at(-1)?.url).toContain("offset=50"));
  });

  it("沒有資料時講清楚", async () => {
    auth.role = "MANAGER";
    stub({ total: 0, items: [] });
    wrap(<AcquisitionRecords />);
    expect(await screen.findByText("沒有符合的收購紀錄。")).toBeTruthy();
  });

  it("按「重新整理」會重新判斷能不能作廢（例如剛去開帳回來）", async () => {
    auth.role = "MANAGER";
    stub();
    const user = userEvent.setup();
    wrap(<AcquisitionRecords />);
    await waitFor(() => rowOf(12));
    const before = listCalls().length;
    await user.click(screen.getByRole("button", { name: "重新整理" }));
    await waitFor(() => expect(listCalls().length).toBeGreaterThan(before));
  });
});
