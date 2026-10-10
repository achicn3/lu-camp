// @vitest-environment jsdom
// POS 線上訂單（docs/44 §4.3；O4c）：徽章數字、清單、帶入結帳（價格變了要看得出差額）、取消、暫停接單。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OnlineOrdersPanel, newOpenOrderIds } from "@/features/online-orders/OnlineOrdersPanel";
import type { components } from "@/lib/api-types";

type Order = components["schemas"]["OnlineOrderRead"];
type Overview = components["schemas"]["OnlineOrdersRead"];
type Cart = components["schemas"]["OnlineCartRead"];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Call = { url: string; method: string; body: unknown };

function stubFetch(route: (url: string, method: string) => Response | null): Call[] {
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

function order(id: number, extra: Partial<Order> = {}): Order {
  return {
    id,
    remote_id: id.toString(16).padStart(32, "0"),
    table_label: "A1",
    service_mode: "DINE_IN",
    total: "300",
    payment_method: "CASH",
    note: null,
    lines: [
      { line_no: 1, item_id: 5, name: "拿鐵（燕麥奶）", option_ids: [9], unit_price: 150, qty: 2, line_total: 300, limited: false },
    ],
    created_at: new Date().toISOString(),
    sync_status: "IMPORTED",
    hold_status: "NONE",
    payment_status: "UNPAID",
    reject_reason: null,
    sale_id: null,
    fulfillment_status: "NONE",
    handover_items: null,
    linepay_paid: false,
    attention: null,
    ...extra,
  };
}

function overview(orders: Order[], extra: Partial<Overview> = {}): Overview {
  return {
    configured: true,
    accepting: true,
    paused_reason: null,
    last_pull_at: new Date().toISOString(),
    last_pull_error: null,
    orders,
    ...extra,
  };
}

function cart(extra: Partial<Cart> = {}): Cart {
  return {
    order_id: 1,
    service_mode: "DINE_IN",
    table_no: "A1",
    note: null,
    lines: [
      { line_no: 1, line_type: "MENU", menu_item_id: 5, catalog_product_id: null, menu_option_ids: [9], experience_id: null, qty: 2, description: "拿鐵（燕麥奶）", online_unit_price: "150", unit_price: "150" },
    ],
    online_total: "300",
    total: "300",
    ...extra,
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("POS 線上訂單", () => {
  it("沒設定線上點餐：不顯示", async () => {
    stubFetch(() => json(overview([], { configured: false })));
    const { container } = wrap(<OnlineOrdersPanel cartEmpty onLoad={() => {}} />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("徽章只算還沒處理的單（未付款、沒取消、沒被拒）", async () => {
    stubFetch(() =>
      json(
        overview([
          order(1),
          order(2, { hold_status: "HELD" }),
          order(3, { hold_status: "REJECTED", reject_reason: "「戚風」今天已售完" }),
          order(4, { sync_status: "SETTLED", payment_status: "PAID", sale_id: 9 }),
          order(5, { sync_status: "VOIDED", payment_status: "CANCELLED" }),
        ]),
      ),
    );
    wrap(<OnlineOrdersPanel cartEmpty onLoad={() => {}} />);
    expect(await screen.findByRole("button", { name: /線上訂單.*2/ })).toBeTruthy();
  });

  it("清單：桌號、品項、合計、狀態；被拒的寫出原因", async () => {
    stubFetch(() =>
      json(
        overview([
          order(1, { note: "少冰" }),
          order(3, { hold_status: "REJECTED", reject_reason: "「戚風」今天已售完", table_label: null, service_mode: "TAKEOUT" }),
        ]),
      ),
    );
    const user = userEvent.setup();
    wrap(<OnlineOrdersPanel cartEmpty onLoad={() => {}} />);
    await user.click(await screen.findByRole("button", { name: /線上訂單/ }));
    const dialog = screen.getByRole("dialog", { name: "線上訂單" });
    const first = within(dialog).getByRole("listitem", { name: /A1/ });
    expect(first.textContent).toMatch(/拿鐵（燕麥奶） ×2/);
    expect(first.textContent).toMatch(/\$300/);
    expect(first.textContent).toMatch(/待付款/);
    expect(first.textContent).toMatch(/少冰/);
    const rejected = within(dialog).getByRole("listitem", { name: /外帶/ });
    expect(rejected.textContent).toMatch(/庫存不足.*戚風/);
    expect(within(rejected).queryByRole("button", { name: "帶入結帳" })).toBeNull();
  });

  it("帶入結帳：價格沒變直接帶入", async () => {
    stubFetch((url) => (url.endsWith("/cart") ? json(cart()) : json(overview([order(1)]))));
    const onLoad = vi.fn();
    const user = userEvent.setup();
    wrap(<OnlineOrdersPanel cartEmpty onLoad={onLoad} />);
    await user.click(await screen.findByRole("button", { name: /線上訂單/ }));
    await user.click(screen.getByRole("button", { name: "帶入結帳" }));
    await waitFor(() => expect(onLoad).toHaveBeenCalledWith(expect.objectContaining({ order_id: 1, total: "300" })));
    expect(screen.queryByRole("dialog", { name: "線上訂單" })).toBeNull();
  });

  it("帶入結帳：價格變了先列出差額，店員確認才帶入", async () => {
    const changed = cart({
      lines: [{ line_no: 1, line_type: "MENU", menu_item_id: 5, catalog_product_id: null, menu_option_ids: [9], experience_id: null, qty: 2, description: "拿鐵（燕麥奶）", online_unit_price: "150", unit_price: "160" }],
      total: "320",
    });
    stubFetch((url) => (url.endsWith("/cart") ? json(changed) : json(overview([order(1)]))));
    const onLoad = vi.fn();
    const user = userEvent.setup();
    wrap(<OnlineOrdersPanel cartEmpty onLoad={onLoad} />);
    await user.click(await screen.findByRole("button", { name: /線上訂單/ }));
    await user.click(screen.getByRole("button", { name: "帶入結帳" }));
    const alert = await screen.findByRole("alertdialog", { name: /價格有變動/ });
    expect(alert.textContent).toMatch(/拿鐵（燕麥奶）.*\$150.*\$160/);
    expect(alert.textContent).toMatch(/\$300.*\$320/);
    expect(onLoad).not.toHaveBeenCalled();
    await user.click(within(alert).getByRole("button", { name: "照現在的價格帶入" }));
    expect(onLoad).toHaveBeenCalled();
  });

  it("購物車還有東西：不能帶入（不蓋掉正在結的單）", async () => {
    stubFetch(() => json(overview([order(1)])));
    const user = userEvent.setup();
    wrap(<OnlineOrdersPanel cartEmpty={false} onLoad={() => {}} />);
    await user.click(await screen.findByRole("button", { name: /線上訂單/ }));
    expect((screen.getByRole("button", { name: "帶入結帳" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/先結完或清空目前的購物車/)).toBeTruthy();
  });

  it("取消要再按一次確認", async () => {
    const calls = stubFetch((url) =>
      url.endsWith("/cancel") ? json(order(1, { sync_status: "VOIDED", payment_status: "CANCELLED" })) : json(overview([order(1)])),
    );
    const user = userEvent.setup();
    wrap(<OnlineOrdersPanel cartEmpty onLoad={() => {}} />);
    await user.click(await screen.findByRole("button", { name: /線上訂單/ }));
    await user.click(screen.getByRole("button", { name: "取消這張" }));
    expect(calls.some((c) => c.url.endsWith("/cancel"))).toBe(false);
    await user.click(screen.getByRole("button", { name: "確定取消" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/online-orders/1/cancel") && c.method === "POST")).toBe(true));
  });

  it("帶回家商品付了錢還沒交：列在清單上、標待交貨，按「已交貨」才結單（docs/63 §13）", async () => {
    const paid = order(7, {
      sync_status: "SETTLED",
      payment_status: "PAID",
      sale_id: 99,
      fulfillment_status: "AWAITING",
      // 客人點 2 包、櫃檯改成 1 包：交貨照實際結帳
      handover_items: [{ catalog_product_id: 41, name: "耶加雪菲 200g", qty: 1 }],
      lines: [
        { line_no: 1, item_id: null, catalog_product_id: 41, name: "耶加雪菲 200g", option_ids: [], unit_price: 450, qty: 2, line_total: 900, limited: true },
        { line_no: 2, item_id: 5, name: "拿鐵", option_ids: [], unit_price: 150, qty: 1, line_total: 150, limited: false },
      ],
    });
    const calls = stubFetch((url, method) =>
      url.endsWith("/online-orders/7/hand-over") && method === "POST"
        ? json({ ...paid, fulfillment_status: "HANDED_OVER" })
        : json(overview([paid])),
    );
    const user = userEvent.setup();
    wrap(<OnlineOrdersPanel cartEmpty onLoad={() => {}} />);
    // 徽章也算待交貨的單，店員才不會忘了
    expect((await screen.findByRole("button", { name: /線上訂單/ })).textContent).toContain("1");
    await user.click(screen.getByRole("button", { name: /線上訂單/ }));
    const row = screen.getByRole("listitem", { name: /桌號 A1/ });
    expect(row.textContent).toContain("已付款・待交貨");
    expect(row.textContent).toContain("要交給客人：耶加雪菲 200g ×1");
    expect(row.textContent).not.toContain("拿鐵 ×1（帶著走）");
    expect(screen.queryByRole("button", { name: "帶入結帳" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "已交貨" }));
    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith("/online-orders/7/hand-over") && c.method === "POST")).toBe(true),
    );
  });

  it("客人選 LINE Pay、還沒付：等客人付款，不能帶入收現金（否則兩邊都收到錢），可以取消", async () => {
    stubFetch(() => json(overview([order(8, { payment_method: "LINE_PAY" })])));
    const user = userEvent.setup();
    wrap(<OnlineOrdersPanel cartEmpty onLoad={() => {}} />);
    await user.click(await screen.findByRole("button", { name: /線上訂單/ }));
    const row = screen.getByRole("listitem", { name: /桌號 A1/ });
    expect(row.textContent).toContain("等待 LINE Pay 付款");
    expect(screen.queryByRole("button", { name: "帶入結帳" })).toBeNull();
    expect(screen.getByRole("button", { name: "取消這張" })).toBeTruthy();
  });

  it("LINE Pay 付好了：自動成立銷售一次，交給 POS 出單（docs/44 §4.4.2）", async () => {
    const paid = order(9, { payment_method: "LINE_PAY", linepay_paid: true });
    const calls = stubFetch((url, method) =>
      url.endsWith("/online-orders/9/settle-paid") && method === "POST"
        ? json({ sale_id: 321 })
        : json(overview([paid])),
    );
    const settled = vi.fn();
    wrap(<OnlineOrdersPanel cartEmpty onLoad={() => {}} onPaidSettled={settled} />);
    await waitFor(() => expect(settled).toHaveBeenCalledWith(321, expect.objectContaining({ id: 9 })));
    await new Promise((r) => setTimeout(r, 50));
    expect(calls.filter((c) => c.url.endsWith("/settle-paid")).length).toBe(1);
  });

  it("自動成立遇到連線問題：不會就此卡住，之後會再試（Codex O5 第一輪）", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const paid = order(11, { payment_method: "LINE_PAY", linepay_paid: true });
      let attempts = 0;
      stubFetch((url, method) => {
        if (url.endsWith("/online-orders/11/settle-paid") && method === "POST") {
          attempts += 1;
          return attempts === 1 ? json({ detail: "伺服器忙" }, 503) : json({ sale_id: 77 });
        }
        return json(overview([paid]));
      });
      const settled = vi.fn();
      wrap(<OnlineOrdersPanel cartEmpty onLoad={() => {}} onPaidSettled={settled} />);
      await waitFor(() => expect(attempts).toBe(1));
      await vi.advanceTimersByTimeAsync(15_000);
      await waitFor(() => expect(settled).toHaveBeenCalledWith(77, expect.objectContaining({ id: 11 })));
    } finally {
      vi.useRealTimers();
    }
  });

  it("成立了但回應沒回來：清單變已結帳時照樣交給 POS 出單（Codex O5 第二輪）", async () => {
    const paid = order(12, { payment_method: "LINE_PAY", linepay_paid: true });
    let settledOnServer = false;
    stubFetch((url, method) => {
      if (url.endsWith("/online-orders/12/settle-paid") && method === "POST") {
        settledOnServer = true;
        throw new TypeError("network down"); // 後端其實成立了，回應遺失
      }
      return json(overview([
        settledOnServer ? { ...paid, sync_status: "SETTLED", payment_status: "PAID", sale_id: 55 } : paid,
      ]));
    });
    const settled = vi.fn();
    wrap(<OnlineOrdersPanel cartEmpty onLoad={() => {}} onPaidSettled={settled} />);
    await waitFor(() => expect(settled).toHaveBeenCalledWith(55, expect.objectContaining({ id: 12 })), { timeout: 8000 });
    expect(settled).toHaveBeenCalledTimes(1);
  });

  it("沒辦法自動成立：寫出原因、不再自動重試", async () => {
    const stuck = order(10, {
      payment_method: "LINE_PAY", linepay_paid: true,
      attention: "客人用 LINE Pay 付了 140 元，POS 現在算 150 元",
    });
    const calls = stubFetch(() => json(overview([stuck])));
    const user = userEvent.setup();
    wrap(<OnlineOrdersPanel cartEmpty onLoad={() => {}} onPaidSettled={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: /線上訂單/ }));
    expect(screen.getByRole("listitem", { name: /桌號 A1/ }).textContent).toContain("POS 現在算 150 元");
    expect(calls.some((c) => c.url.endsWith("/settle-paid"))).toBe(false);
  });

  it("暫停接單送到雲端", async () => {
    const calls = stubFetch((url, method) =>
      url.endsWith("/accepting") && method === "PUT" ? json(overview([], { accepting: false })) : json(overview([])),
    );
    const user = userEvent.setup();
    wrap(<OnlineOrdersPanel cartEmpty onLoad={() => {}} />);
    await user.click(await screen.findByRole("button", { name: /線上訂單/ }));
    await user.click(screen.getByRole("button", { name: "暫停接單" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ accepting: false }));
    expect(await screen.findByText("暫停接單中")).toBeTruthy();
    expect(screen.getByRole("button", { name: "恢復接單" })).toBeTruthy();
  });

  it("雲端自動暫停：寫出原因，可以恢復", async () => {
    const calls = stubFetch((url, method) =>
      url.endsWith("/accepting") && method === "PUT"
        ? json(overview([]))
        : json(overview([], { accepting: false, paused_reason: "異常大量下單，已自動暫停" })),
    );
    const user = userEvent.setup();
    wrap(<OnlineOrdersPanel cartEmpty onLoad={() => {}} />);
    expect(await screen.findByRole("button", { name: /線上訂單.*暫停中/ })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /線上訂單/ }));
    expect(screen.getByText(/暫停接單中：異常大量下單/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "恢復接單" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ accepting: true }));
  });

  it("連不上雲端：寫出來", async () => {
    stubFetch(() => json(overview([], { last_pull_error: "連不上線上點餐雲端" })));
    const user = userEvent.setup();
    wrap(<OnlineOrdersPanel cartEmpty onLoad={() => {}} />);
    await user.click(await screen.findByRole("button", { name: /線上訂單/ }));
    expect(screen.getByRole("alert").textContent).toMatch(/連不上線上點餐雲端/);
  });
});

describe("新單提示", () => {
  it("只對第一次出現、還沒處理的單響", () => {
    expect(newOpenOrderIds(null, [order(1)])).toEqual([]); // 第一次載入不響
    expect(newOpenOrderIds(new Set([1]), [order(1), order(2)])).toEqual([2]);
    expect(newOpenOrderIds(new Set([1]), [order(1), order(3, { hold_status: "REJECTED" })])).toEqual([]);
  });
});
