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
  row({ id: 13, type: "BULK_LOT", item_count: 1, item_names: ["營釘一批"] }),
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
      if (url.endsWith("/void-items")) {
        return json([
          { id: 31, item_code: "I31", name: "營燈", acquisition_cost: "1000", status: "IN_STOCK", voided: false },
          { id: 32, item_code: "I32", name: "睡袋", acquisition_cost: "1000", status: "IN_STOCK", voided: false },
          { id: 33, item_code: "I33", name: "爐頭", acquisition_cost: "1000", status: "SOLD", voided: false },
        ]);
      }
      if (/\/acquisitions\/\d+$/.test(url)) return json(row());
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

  it("改成付現：只有管理者、只出現在全額購物金撥款的單（店主 2026-10-10）", async () => {
    auth.role = "MANAGER";
    stub();
    wrap(<AcquisitionRecords />);
    await waitFor(() => rowOf(12));
    expect(within(rowOf(9)).getByRole("button", { name: "改成付現" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "改成付現" })).toHaveLength(1);
    cleanup();
    auth.role = "CLERK";
    wrap(<AcquisitionRecords />);
    await waitFor(() => rowOf(12));
    expect(screen.queryByRole("button", { name: "改成付現" })).toBeNull();
  });

  it("管理者：可作廢的單有作廢鈕；不能作廢的單反灰並講原因", async () => {
    auth.role = "MANAGER";
    stub();
    wrap(<AcquisitionRecords />);
    await waitFor(() => rowOf(12));

    const ok = within(rowOf(12)).getByRole("button", { name: "作廢" }) as HTMLButtonElement;
    expect(ok.disabled).toBe(false);
    // 買斷單已有商品賣出：整張不能作廢，但其餘商品仍可逐件作廢 → 鈕可按、講清楚只能作廢其餘
    const partial = rowOf(10);
    expect((within(partial).getByRole("button", { name: "作廢" }) as HTMLButtonElement).disabled).toBe(false);
    expect(partial.textContent).toContain("部分商品已賣出或已作廢，其餘可勾選作廢");
    // 購物金已被用掉：整張沖不回，但只作廢幾件可能沖得回 → 鈕可按、講清楚
    const credit = rowOf(9);
    expect((within(credit).getByRole("button", { name: "作廢" }) as HTMLButtonElement).disabled).toBe(false);
    expect(credit.textContent).toContain("購物金已被用掉");
    expect(screen.queryByRole("button", { name: "選品作廢" })).toBeNull(); // 只有一顆作廢鈕
    const expectations: [number, string][] = [
      [11, "寄售"],
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

  it("散裝單按作廢 → 整張作廢：填原因確認 → 顯示結果並重新整理清單", async () => {
    auth.role = "MANAGER";
    stub();
    const user = userEvent.setup();
    wrap(<AcquisitionRecords />);
    await waitFor(() => rowOf(13));
    const before = listCalls().length;

    await user.click(within(rowOf(13)).getByRole("button", { name: "作廢" }));
    const dialog = await screen.findByRole("dialog", { name: "作廢收購確認" });
    await user.type(within(dialog).getByLabelText("作廢原因"), "登錄錯誤");
    await user.click(within(dialog).getByRole("button", { name: "確認作廢" }));

    expect(await screen.findByText(/已作廢收購單 #12/)).toBeTruthy();
    expect(requests.some((r) => r.method === "POST" && r.url.includes("/acquisitions/13/void"))).toBe(true);
    await waitFor(() => expect(listCalls().length).toBeGreaterThan(before));
  });

  it("買斷單按作廢 → 跳出商品勾選視窗，預設勾好所有可作廢的商品（等於整張作廢）", async () => {
    auth.role = "MANAGER";
    stub();
    const user = userEvent.setup();
    wrap(<AcquisitionRecords />);
    await waitFor(() => rowOf(12));

    await user.click(within(rowOf(12)).getByRole("button", { name: "作廢" }));
    const dialog = await screen.findByRole("dialog", { name: "作廢收購 #12" });
    const lamp = (await within(dialog).findByRole("checkbox", { name: /營燈/ })) as HTMLInputElement;
    const bag = within(dialog).getByRole("checkbox", { name: /睡袋/ }) as HTMLInputElement;
    const sold = within(dialog).getByRole("checkbox", { name: /爐頭/ }) as HTMLInputElement;
    expect(lamp.checked && bag.checked).toBe(true);
    expect(sold.checked || !sold.disabled).toBe(false); // 已售的不勾、也勾不了

    await user.click(bag); // 睡袋留下
    await user.click(within(dialog).getByRole("button", { name: "作廢收購" }));
    const confirm = await screen.findByRole("dialog", { name: "作廢收購確認" });
    expect(confirm.textContent).toContain("本次作廢 1 件商品");
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


it("含已售商品的收購仍可從紀錄選擇待整理商品作廢", async () => {
  auth.role = "MANAGER";
  let submitted: unknown;
  vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
    if (request.url.endsWith("/void-items")) return json([
      { id: 21, item_code: "I21", name: "待整理帳篷", acquisition_cost: "1000", status: "PENDING_LISTING", voided: false },
      { id: 22, item_code: "I22", name: "已售睡袋", acquisition_cost: "1000", status: "SOLD", voided: false },
    ]);
    if (request.method === "POST") {
      submitted = await request.json();
      return json({ acquisition_id: 10, voided_at: "2026-09-30T00:00:00Z", reversed_cash: "1000", reversed_credit: "0", fully_voided: false, item_ids: [21] });
    }
    if (request.url.endsWith("/acquisitions/10")) return json(row({ id: 10 }));
    return json({ total: 1, items: [row({ id: 10, void_block: "HAS_SOLD_ITEMS" })] });
  }));
  const user = userEvent.setup();
  wrap(<AcquisitionRecords />);
  const button = await screen.findByRole("button", { name: "作廢" });
  await user.click(button);
  const pending = (await screen.findByRole("checkbox", { name: /待整理帳篷/ })) as HTMLInputElement;
  expect((screen.getByRole("checkbox", { name: /已售睡袋/ }) as HTMLInputElement).disabled).toBe(true);
  expect(pending.checked).toBe(true); // 預設勾好可作廢的
  await user.click(screen.getByRole("button", { name: "作廢收購" }));
  await user.type(screen.getByLabelText("作廢原因"), "只退待整理帳篷");
  await user.click(screen.getByRole("button", { name: "確認作廢" }));
  await waitFor(() => expect(submitted).toEqual({ reason: "只退待整理帳篷", item_ids: [21] }));
  expect(await screen.findByText(/已作廢所選商品/)).toBeTruthy();
});

it("作廢的商品勾選以對話視窗開在畫面上（不是加在清單最底下讓人看不到），可關閉", async () => {
  // 2026-10-02 正式機：清單有幾十列時，選品作廢區塊被加在翻頁列下方、在可視範圍外，
  // 店長按了像是沒反應（log 顯示連點 #255、#254、#253，每次商品其實都已載入）。
  auth.role = "MANAGER";
  vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
    if (request.url.endsWith("/void-items")) return json([
      { id: 21, item_code: "I21", name: "待整理帳篷", acquisition_cost: "1000", status: "IN_STOCK", voided: false },
    ]);
    if (request.url.endsWith("/acquisitions/10")) return json(row({ id: 10 }));
    return json({ total: 1, items: [row({ id: 10 })] });
  }));
  const user = userEvent.setup();
  wrap(<AcquisitionRecords />);
  await user.click(await screen.findByRole("button", { name: "作廢" }));

  const dialog = await screen.findByRole("dialog", { name: "作廢收購 #10" });
  expect(dialog.getAttribute("aria-modal")).toBe("true");
  expect(await within(dialog).findByRole("checkbox", { name: /待整理帳篷/ })).toBeTruthy();

  await user.click(within(dialog).getByRole("button", { name: "關閉作廢視窗" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "作廢收購 #10" })).toBeNull());

  await user.click(screen.getByRole("button", { name: "作廢" }));
  await screen.findByRole("dialog", { name: "作廢收購 #10" });
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "作廢收購 #10" })).toBeNull());
});

function voidFlowStub(listRow: Record<string, unknown>, voidItems: unknown[]) {
  const posted: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
    if (request.url.endsWith("/void-items")) return json(voidItems);
    if (request.method === "POST") {
      const body = (await request.json()) as { item_ids: number[] };
      posted.push(body);
      return json({ acquisition_id: 12, voided_at: "2026-10-02T00:00:00Z", reversed_cash: "2000", reversed_credit: "0", fully_voided: true, item_ids: body.item_ids });
    }
    if (/\/acquisitions\/\d+$/.test(request.url)) return json(row(listRow));
    return json({ total: 1, items: [row(listRow)] });
  }));
  return posted;
}

const TWO_IN_STOCK = [
  { id: 31, item_code: "I31", name: "營燈", acquisition_cost: "1000", status: "IN_STOCK", voided: false },
  { id: 32, item_code: "I32", name: "睡袋", acquisition_cost: "1000", status: "IN_STOCK", voided: false },
];

it("買斷單全勾直接送出＝整張作廢：帶上所有可作廢商品", async () => {
  auth.role = "MANAGER";
  const posted = voidFlowStub({}, TWO_IN_STOCK);
  const user = userEvent.setup();
  wrap(<AcquisitionRecords />);
  await user.click(await screen.findByRole("button", { name: "作廢" }));
  const dialog = await screen.findByRole("dialog", { name: "作廢收購 #12" });
  await within(dialog).findByRole("checkbox", { name: /營燈/ });
  await user.click(within(dialog).getByRole("button", { name: "作廢收購" }));
  await user.type(screen.getByLabelText("作廢原因"), "整張登錄錯誤");
  await user.click(screen.getByRole("button", { name: "確認作廢" }));

  await waitFor(() => expect(posted).toEqual([{ reason: "整張登錄錯誤", item_ids: [31, 32] }]));
  expect(await within(dialog).findByText(/已作廢收購單 #12/)).toBeTruthy();
});

it("確認視窗開著時按 Esc 不會把外層勾選視窗整個關掉（已勾的與原因不會丟）", async () => {
  auth.role = "MANAGER";
  voidFlowStub({}, TWO_IN_STOCK);
  const user = userEvent.setup();
  wrap(<AcquisitionRecords />);
  await user.click(await screen.findByRole("button", { name: "作廢" }));
  const dialog = await screen.findByRole("dialog", { name: "作廢收購 #12" });
  await within(dialog).findByRole("checkbox", { name: /營燈/ });
  await user.click(within(dialog).getByRole("button", { name: "作廢收購" }));
  await screen.findByRole("dialog", { name: "作廢收購確認" });

  await user.keyboard("{Escape}");

  expect(screen.getByRole("dialog", { name: "作廢收購 #12" })).toBeTruthy();
});

it("已上架的商品也能作廢：待整理與已上架混在一起，一樣預設全勾（店主 2026-10-02）", async () => {
  auth.role = "MANAGER";
  const posted = voidFlowStub({}, [
    { id: 31, item_code: "I31", name: "營燈", acquisition_cost: "1000", status: "PENDING_LISTING", voided: false },
    { id: 32, item_code: "I32", name: "睡袋", acquisition_cost: "1000", status: "IN_STOCK", voided: false },
  ]);
  const user = userEvent.setup();
  wrap(<AcquisitionRecords />);
  await user.click(await screen.findByRole("button", { name: "作廢" }));
  const dialog = await screen.findByRole("dialog", { name: "作廢收購 #12" });
  const lamp = (await within(dialog).findByRole("checkbox", { name: /營燈/ })) as HTMLInputElement;
  const bag = within(dialog).getByRole("checkbox", { name: /睡袋/ }) as HTMLInputElement;
  expect(lamp.checked && bag.checked).toBe(true);
  await user.click(within(dialog).getByRole("button", { name: "作廢收購" }));
  await user.type(screen.getByLabelText("作廢原因"), "全退");
  await user.click(screen.getByRole("button", { name: "確認作廢" }));
  await waitFor(() => expect(posted).toEqual([{ reason: "全退", item_ids: [31, 32] }]));
});
