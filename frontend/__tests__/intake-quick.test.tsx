// @vitest-environment jsdom
// 排隊收購快速估價與客人勾選（docs/42 §13；店主 2026-10-02）。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CustomerChecklist } from "@/features/intake/CustomerChecklist";
import { QuickEstimate } from "@/features/intake/QuickEstimate";
import type { components } from "@/lib/api-types";

type Batch = components["schemas"]["IntakeBatchRead"];
type Line = components["schemas"]["IntakeLineRead"];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Call = { url: string; method: string; body: unknown };

function stubFetch(route: (url: string, method: string) => Response | null = () => null): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      const text = input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
      calls.push({ url, method, body: text ? (JSON.parse(text) as unknown) : null });
      return route(url, method) ?? json({});
    }),
  );
  return calls;
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

function line(n: number, extra: Partial<Line> = {}): Line {
  return {
    id: 100 + n,
    line_no: n,
    short_name: `第 ${n} 件`,
    qty: 1,
    acquisition_type: "BUYOUT",
    reference_price: null,
    discount_pct: null,
    expected_listed_price: null,
    suggested_cost: null,
    deal_cost: null,
    commission_pct: null,
    grade: null,
    category_id: null,
    brand_id: null,
    product_model_id: null,
    note: null,
    disposition: "PENDING",
    accepted_qty: 0,
    returned_to_customer: false,
    ...extra,
  } as Line;
}

function batch(lines: Line[], extra: Partial<Batch> = {}): Batch {
  return {
    id: 7,
    ticket_date: "2026-10-02",
    ticket_no: 12,
    ticket_label: "A012",
    slip_code: "IN000007",
    contact_id: 1,
    contact_name: "王小明",
    declared_item_count: lines.length,
    status: "PENDING_ESTIMATE",
    note: null,
    created_at: "2026-10-02T02:00:00Z",
    cancel_reason: null,
    line_count: lines.length,
    item_count: lines.length,
    priced_item_count: lines.filter((l) => l.deal_cost !== null).length,
    deal_total: "0",
    accepted_item_count: 0,
    accepted_total: "0",
    signature_task_id: null,
    paid_at: null,
    acquisition_ids: [],
    lines,
    ...extra,
  } as Batch;
}

const RATES = { taxRate: 0.05, feeRate: 0.022, marginPct: 45 };

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("快速估價", () => {
  it("每件一個大輸入框；按 Enter 存這件並跳到下一件", async () => {
    const calls = stubFetch();
    const user = userEvent.setup();
    wrap(<QuickEstimate batch={batch([line(1), line(2), line(3)])} rates={RATES} defaultCommissionPct={50} onChanged={() => {}} />);
    const first = screen.getByLabelText("1 號 收購價");
    expect(screen.getAllByRole("textbox", { name: /號 收購價$/ })).toHaveLength(3);
    expect(first.getAttribute("inputmode")).toBe("numeric");
    await user.click(first);
    await user.type(first, "300{Enter}");
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")).toMatchObject({ body: { deal_cost: "300" } }),
    );
    expect(calls.find((c) => c.method === "PATCH")?.url).toMatch(/\/intake-batches\/7\/lines\/101$/);
    expect(document.activeElement).toBe(screen.getByLabelText("2 號 收購價"));
  });

  it("沒改的不重存；不是整數元提示錯誤、不送出", async () => {
    const calls = stubFetch();
    const user = userEvent.setup();
    wrap(<QuickEstimate batch={batch([line(1, { deal_cost: "300" }), line(2)])} rates={RATES} defaultCommissionPct={50} onChanged={() => {}} />);
    await user.click(screen.getByLabelText("1 號 收購價"));
    await user.keyboard("{Enter}");
    const second = screen.getByLabelText("2 號 收購價");
    await user.type(second, "12.5{Enter}");
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(0);
  });

  it("「詳細」展開現有欄位，存檔帶回所有填的內容", async () => {
    const calls = stubFetch();
    const user = userEvent.setup();
    wrap(<QuickEstimate batch={batch([line(1, { deal_cost: "300" })])} rates={RATES} defaultCommissionPct={50} onChanged={() => {}} />);
    await user.click(screen.getByRole("button", { name: "1 號 詳細" }));
    const form = screen.getByRole("form", { name: /修改第 1 列/ });
    const name = within(form).getByLabelText("商品簡稱");
    await user.clear(name);
    await user.type(name, "Coleman 營燈");
    await user.click(within(form).getByRole("button", { name: "儲存詳細" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toMatchObject({ short_name: "Coleman 營燈" }),
    );
  });

  it("全部填好才能按「估完，給客人確認」；多一件會新增一列", async () => {
    const calls = stubFetch();
    const user = userEvent.setup();
    const { rerender } = wrap(
      <QuickEstimate batch={batch([line(1, { deal_cost: "300" }), line(2)])} rates={RATES} defaultCommissionPct={50} onChanged={() => {}} />,
    );
    const ready = screen.getByRole("button", { name: "估完，給客人確認" }) as HTMLButtonElement;
    expect(ready.disabled).toBe(true);
    expect(screen.getByText(/已填 1／2 件/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "＋ 多一件" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST")).toMatchObject({
        body: { short_name: "第 3 件", qty: 1, acquisition_type: "BUYOUT" },
      }),
    );
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <QuickEstimate batch={batch([line(1, { deal_cost: "300" }), line(2, { deal_cost: "100" })])} rates={RATES} defaultCommissionPct={50} onChanged={() => {}} />
      </QueryClientProvider>,
    );
    const enabled = screen.getByRole("button", { name: "估完，給客人確認" }) as HTMLButtonElement;
    expect(enabled.disabled).toBe(false);
    await user.click(enabled);
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/intake-batches/7/ready"))).toBe(true));
  });

  it("存檔失敗：可以再按一次重試；沒存好前不能估完（不會用舊價格成交）", async () => {
    let fail = true;
    const calls = stubFetch((_url, method) => {
      if (method === "PATCH") return fail ? json({ detail: "網路斷了" }, 500) : json({});
      return null;
    });
    const user = userEvent.setup();
    wrap(
      <QuickEstimate
        batch={batch([line(1, { deal_cost: "100" }), line(2, { deal_cost: "50" })])}
        rates={RATES}
        defaultCommissionPct={50}
        onChanged={() => {}}
      />,
    );
    const first = screen.getByLabelText("1 號 收購價");
    await user.clear(first);
    await user.type(first, "200{Enter}");
    expect((await screen.findByRole("alert")).textContent).toContain("網路斷了");
    const ready = screen.getByRole("button", { name: "估完，給客人確認" }) as HTMLButtonElement;
    expect(ready.disabled).toBe(true);
    expect(screen.getByText(/還有收購價沒存好/)).toBeTruthy();
    fail = false;
    await user.click(first);
    await user.keyboard("{Enter}");
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "PATCH").map((c) => c.body)).toEqual([
      { deal_cost: "200" },
      { deal_cost: "200" },
    ]);
  });

  it("改了還沒按 Enter／沒離開欄位：不能估完", async () => {
    stubFetch();
    const user = userEvent.setup();
    wrap(
      <QuickEstimate
        batch={batch([line(1, { deal_cost: "100" })])}
        rates={RATES}
        defaultCommissionPct={50}
        onChanged={() => {}}
      />,
    );
    const first = screen.getByLabelText("1 號 收購價");
    await user.clear(first);
    await user.type(first, "300");
    expect((screen.getByRole("button", { name: "估完，給客人確認" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("寄售的件不用填收購價（看抽成）", () => {
    stubFetch();
    wrap(
      <QuickEstimate
        batch={batch([line(1, { acquisition_type: "CONSIGNMENT", commission_pct: 50, expected_listed_price: "800" })])}
        rates={RATES}
        defaultCommissionPct={50}
        onChanged={() => {}}
      />,
    );
    expect(screen.queryByLabelText("1 號 收購價")).toBeNull();
    expect(screen.getByText(/寄售/)).toBeTruthy();
  });
});

describe("客人勾選要賣哪幾件", () => {
  const ready = batch(
    [
      line(1, { deal_cost: "300", disposition: "ACCEPTED", accepted_qty: 1, short_name: "Coleman 營燈" }),
      line(2, { deal_cost: "500", disposition: "ACCEPTED", accepted_qty: 1 }),
      line(3, { deal_cost: "200", disposition: "CUSTOMER_KEPT", accepted_qty: 0, returned_to_customer: true }),
    ],
    { status: "AWAITING_CONFIRM" },
  );

  it("逐件列出號碼與收購價、預設照目前勾選；取消勾選總額即時更新", async () => {
    stubFetch();
    const user = userEvent.setup();
    wrap(<CustomerChecklist batch={ready} onDone={() => {}} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: /確認要賣的商品/ });
    const one = within(dialog).getByRole("checkbox", { name: /1 號.*Coleman 營燈.*\$300/ });
    const three = within(dialog).getByRole("checkbox", { name: /3 號.*\$200/ });
    expect((one as HTMLInputElement).checked).toBe(true);
    expect((three as HTMLInputElement).checked).toBe(false);
    const total = () => within(dialog).getByRole("status").textContent ?? "";
    expect(total()).toMatch(/共 2 件.*\$800/);
    await user.click(one);
    expect(total()).toMatch(/共 1 件.*\$500/);
    // 客人畫面不出現成本毛利、售價等店內資訊
    expect(dialog.textContent).not.toMatch(/毛利|預計售價|成本|建議/);
    // 「第 N 件」這種預設名稱不重複顯示
    expect(dialog.textContent).not.toContain("第 2 件");
  });

  it("按確認：送出不賣的那幾件，完成後請客人交還店員", async () => {
    const calls = stubFetch(() => json(ready));
    const onDone = vi.fn();
    const user = userEvent.setup();
    wrap(<CustomerChecklist batch={ready} onDone={onDone} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: /確認要賣的商品/ });
    await user.click(within(dialog).getByRole("checkbox", { name: /2 號/ }));
    await user.click(within(dialog).getByRole("button", { name: /確認/ }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST")).toMatchObject({ body: { kept_line_ids: [102, 103] } }),
    );
    expect(await within(dialog).findByText(/請把平板交還給店員/)).toBeTruthy();
    expect(onDone).toHaveBeenCalled();
  });

  it("全部都不勾：不能確認，提示請店員處理", async () => {
    stubFetch();
    const user = userEvent.setup();
    wrap(<CustomerChecklist batch={ready} onDone={() => {}} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: /確認要賣的商品/ });
    await user.click(within(dialog).getByRole("checkbox", { name: /1 號/ }));
    await user.click(within(dialog).getByRole("checkbox", { name: /2 號/ }));
    expect((within(dialog).getByRole("button", { name: /確認/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(dialog).getByText(/都不賣.*店員/)).toBeTruthy();
  });
});
