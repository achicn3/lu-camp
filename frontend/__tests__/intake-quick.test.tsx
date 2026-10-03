// @vitest-environment jsdom
// 排隊收購快速估價與客人勾選（docs/42 §13；店主 2026-10-02）。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CustomerChecklist } from "@/features/intake/CustomerChecklist";
import { QuickEstimate } from "@/features/intake/QuickEstimate";
import { IntakeSteps } from "@/features/intake/StatusBadge";
import type { components } from "@/lib/api-types";

vi.mock("@/app/kiosk/SignatureCanvas", async () => {
  const React = await import("react");
  return {
    SignatureCanvas: React.forwardRef<
      { toBase64(): string; clear(): void },
      { onInkChange: (hasInk: boolean) => void }
    >(function FakeSignatureCanvas({ onInkChange }, ref) {
      React.useImperativeHandle(ref, () => ({
        toBase64: () => "signature-png-base64",
        clear: () => onInkChange(false),
      }));
      return (
        <button type="button" onClick={() => onInkChange(true)}>
          模擬簽名
        </button>
      );
    }),
  };
});

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

  it("「詳細」填原價、按折數：自動帶出建議收購價，存檔一起送出（店主 2026-10-04）", async () => {
    const calls = stubFetch();
    const user = userEvent.setup();
    wrap(<QuickEstimate batch={batch([line(1)])} rates={RATES} defaultCommissionPct={50} onChanged={() => {}} />);
    await user.click(screen.getByRole("button", { name: "1 號 詳細" }));
    const form = screen.getByRole("form", { name: /修改第 1 列/ });
    await user.type(within(form).getByLabelText("原價／件"), "1000");
    await user.click(within(form).getByRole("button", { name: "5折" }));
    expect((within(form).getByLabelText("收購價／件") as HTMLInputElement).value).toBe("256");
    await user.click(within(form).getByRole("button", { name: "儲存詳細" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toMatchObject({
        reference_price: "1000",
        discount_pct: 50,
        expected_listed_price: "500",
        suggested_cost: "256",
        deal_cost: "256",
      }),
    );
  });

  it("「詳細」：已經填過收購價就不被建議價蓋掉，只顯示建議", async () => {
    const calls = stubFetch();
    const user = userEvent.setup();
    wrap(
      <QuickEstimate batch={batch([line(1, { deal_cost: "300" })])} rates={RATES} defaultCommissionPct={50} onChanged={() => {}} />,
    );
    await user.click(screen.getByRole("button", { name: "1 號 詳細" }));
    const form = screen.getByRole("form", { name: /修改第 1 列/ });
    await user.type(within(form).getByLabelText("原價／件"), "1000");
    await user.click(within(form).getByRole("button", { name: "5折" }));
    expect((within(form).getByLabelText("收購價／件") as HTMLInputElement).value).toBe("300");
    expect(within(form).getByText("建議 $256")).toBeTruthy();
    await user.click(within(form).getByRole("button", { name: "儲存詳細" }));
    // 沒在「詳細」改收購價就不送，免得蓋掉列上剛存的價
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")).toBeTruthy());
    expect(calls.find((c) => c.method === "PATCH")?.body).not.toHaveProperty("deal_cost");
  });

  it("「詳細」：散裝與寄售不出現每件收購價（散裝在外面填整堆總價、寄售填寄售售價）", async () => {
    stubFetch();
    const user = userEvent.setup();
    wrap(
      <QuickEstimate
        batch={batch([line(1, { acquisition_type: "BULK_LOT" }), line(2, { acquisition_type: "CONSIGNMENT", commission_pct: 50 })])}
        rates={RATES}
        defaultCommissionPct={50}
        onChanged={() => {}}
      />,
    );
    await user.click(screen.getByRole("button", { name: "1 號 詳細" }));
    await user.click(screen.getByRole("button", { name: "2 號 詳細" }));
    for (const n of [1, 2]) {
      const form = screen.getByRole("form", { name: new RegExp(`修改第 ${n} 列`) });
      expect(within(form).queryByLabelText("收購價／件")).toBeNull();
    }
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
    expect(screen.getByText(/還有價格沒存好/)).toBeTruthy();
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

  it("寄售的件填寄售售價（不是收購價）", async () => {
    const calls = stubFetch();
    const user = userEvent.setup();
    wrap(
      <QuickEstimate
        batch={batch([line(1, { acquisition_type: "CONSIGNMENT", commission_pct: 50 })])}
        rates={RATES}
        defaultCommissionPct={50}
        onChanged={() => {}}
      />,
    );
    expect(screen.queryByLabelText("1 號 收購價")).toBeNull();
    await user.type(screen.getByLabelText("1 號 寄售售價"), "3000{Enter}");
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ expected_listed_price: "3000" }),
    );
    // 沒填寄售售價不算估好
    expect(screen.getByText(/已填 0／1 件/)).toBeTruthy();
  });
});

describe("估價時直接選類型", () => {
  function typeButtons(n: number) {
    return within(screen.getByRole("group", { name: `${n} 號 類型` }));
  }

  it("預設二手；四個類型按鈕，輸入框不跟著變窄", () => {
    stubFetch();
    wrap(<QuickEstimate batch={batch([line(1)])} rates={RATES} defaultCommissionPct={50} onChanged={() => {}} />);
    const group = typeButtons(1);
    expect(group.getAllByRole("button").map((b) => b.textContent)).toEqual(["二手", "全新", "散裝", "寄售"]);
    expect(group.getByRole("button", { name: "二手" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("按全新＝買斷、成色全新；再按二手把全新拿掉", async () => {
    const calls = stubFetch();
    const user = userEvent.setup();
    const { rerender } = wrap(
      <QuickEstimate batch={batch([line(1)])} rates={RATES} defaultCommissionPct={50} onChanged={() => {}} />,
    );
    await user.click(typeButtons(1).getByRole("button", { name: "全新" }));
    await waitFor(() =>
      expect(calls.at(-1)?.body).toEqual({ acquisition_type: "BUYOUT", grade: "N" }),
    );
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <QuickEstimate batch={batch([line(1, { grade: "N" })])} rates={RATES} defaultCommissionPct={50} onChanged={() => {}} />
      </QueryClientProvider>,
    );
    expect(typeButtons(1).getByRole("button", { name: "全新" }).getAttribute("aria-pressed")).toBe("true");
    await user.click(typeButtons(1).getByRole("button", { name: "二手" }));
    await waitFor(() =>
      expect(calls.at(-1)?.body).toEqual({ acquisition_type: "BUYOUT", grade: null }),
    );
  });

  it("按寄售換類型；按散裝出現整堆總價與件數（件數可不填）", async () => {
    const calls = stubFetch();
    const user = userEvent.setup();
    wrap(
      <QuickEstimate
        batch={batch([line(1), line(2, { acquisition_type: "BULK_LOT" })])}
        rates={RATES}
        defaultCommissionPct={50}
        onChanged={() => {}}
      />,
    );
    await user.click(typeButtons(1).getByRole("button", { name: "寄售" }));
    await waitFor(() => expect(calls.at(-1)?.body).toEqual({ acquisition_type: "CONSIGNMENT", grade: null }));
    expect(screen.getByLabelText("2 號 整堆總價")).toBeTruthy();
    const pieces = screen.getByLabelText("2 號 件數（可不填）");
    await user.type(pieces, "10{Enter}");
    await waitFor(() => expect(calls.at(-1)?.body).toEqual({ bulk_piece_count: 10 }));
  });

  it("散裝的總價是整堆的，不是乘上件數：畫面寫出整堆多少、每件約多少", () => {
    stubFetch();
    wrap(
      <QuickEstimate
        batch={batch([line(1, { acquisition_type: "BULK_LOT", deal_cost: "55", bulk_piece_count: 10 })])}
        rates={RATES}
        defaultCommissionPct={50}
        onChanged={() => {}}
      />,
    );
    expect(screen.getByText("整堆 $55，共 10 件，每件約 $5.5")).toBeTruthy();
    expect(screen.getByText(/收購價合計/).textContent).toContain("$55");
  });

  it("存檔還沒回來又改了價：新的值不會被舊結果蓋掉，也不能先估完", async () => {
    const calls: { body: unknown }[] = [];
    const pending: ((r: Response) => void)[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const req = input as Request;
        calls.push({ body: JSON.parse(await req.clone().text()) as unknown });
        return new Promise<Response>((resolve) => pending.push(resolve));
      }),
    );
    const user = userEvent.setup();
    const { rerender } = wrap(
      <QuickEstimate batch={batch([line(1)])} rates={RATES} defaultCommissionPct={50} onChanged={() => {}} />,
    );
    const input = screen.getByLabelText("1 號 收購價") as HTMLInputElement;
    await user.type(input, "100{Enter}");
    await waitFor(() => expect(calls).toHaveLength(1));
    await user.click(input);
    await user.clear(input);
    await user.type(input, "200");
    // 第一次存檔（100）現在才回來，畫面重新整理成伺服器上的 100
    pending[0](json(line(1, { deal_cost: "100" })));
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <QuickEstimate batch={batch([line(1, { deal_cost: "100" })])} rates={RATES} defaultCommissionPct={50} onChanged={() => {}} />
      </QueryClientProvider>,
    );
    expect((screen.getByLabelText("1 號 收購價") as HTMLInputElement).value).toBe("200");
    expect((screen.getByRole("button", { name: "估完，給客人確認" }) as HTMLButtonElement).disabled).toBe(true);
    await user.keyboard("{Enter}");
    await waitFor(() => expect(calls.at(-1)?.body).toEqual({ deal_cost: "200" }));
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

  function task(id: number, total: string, names: string[]): Omit<components["schemas"]["KioskTaskRead"], "content"> & {
    content: Record<string, unknown>;
  } {
    return {
      id,
      kind: "ACQUISITION_AFFIDAVIT",
      status: "PENDING",
      content: {
        items: names.map((name, i) => ({ name, amount: i === 0 ? "300" : "500" })),
        total,
        seller_name: "王小明",
        store_credit_premium: { rate: "0.1", amount: String(Math.round(Number(total) * 1.1)), extra: String(Math.round(Number(total) * 0.1)) },
      },
      chosen_payout: null,
      expires_at: "2026-10-02T03:00:00Z",
      agreement_title: "收購切結書",
      agreement_body: "本人保證出售之物品為本人合法所有。",
      consent_mode: "SIGNATURE",
    };
  }

  function signingRoutes(tasks: ReturnType<typeof task>[]) {
    let started = 0;
    return (url: string, method: string): Response | null => {
      if (url.endsWith("/customer-confirm")) return json(ready);
      if (url.endsWith("/tablet-signature") && method === "POST") return json(tasks[Math.min(started++, tasks.length - 1)]);
      if (url.includes("/tablet-sign")) return json({ ...tasks[tasks.length - 1], status: "SIGNED", chosen_payout: "STORE_CREDIT" });
      return null;
    };
  }

  it("勾選頁只有「確認」一個按鈕（不再有交給店員）", () => {
    stubFetch();
    wrap(<CustomerChecklist batch={ready} onDone={() => {}} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: /確認要賣的商品/ });
    expect(within(dialog).queryByRole("button", { name: /交給店員/ })).toBeNull();
    expect(within(dialog).getAllByRole("button").map((b) => b.textContent)).toEqual(["確認"]);
  });

  it("按確認：送出不賣的那幾件，直接進簽署頁，顯示要賣的商品、合計與切結書", async () => {
    const calls = stubFetch(signingRoutes([task(55, "800", ["Coleman 營燈", "第 2 件"])]));
    const user = userEvent.setup();
    wrap(<CustomerChecklist batch={ready} onDone={() => {}} onClose={() => {}} />);
    let dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("checkbox", { name: /2 號/ }));
    await user.click(within(dialog).getByRole("button", { name: /確認/ }));
    await waitFor(() =>
      expect(calls.find((c) => c.url.endsWith("/customer-confirm"))).toMatchObject({
        body: { kept_line_ids: [102, 103] },
      }),
    );
    dialog = await screen.findByRole("dialog", { name: /簽署切結書/ });
    expect(calls.some((c) => c.url.endsWith("/intake-batches/7/tablet-signature") && c.method === "POST")).toBe(true);
    expect(within(dialog).getByText("Coleman 營燈")).toBeTruthy();
    expect(within(dialog).getByText(/本人保證出售之物品/)).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: /現金.*\$800/ })).toBeTruthy();
    // 不寫多得多少錢，固定寫多拿幾 %（店主 2026-10-03）
    const credit = within(dialog).getByRole("button", { name: /購物金/ });
    expect(credit.textContent).toMatch(/\$880/);
    expect(credit.textContent).toMatch(/多拿 10% 購物金/);
    expect(credit.textContent).not.toMatch(/多得|\$80\b/);
  });

  it("簽署頁：要同意條款、選收款方式、簽名才能送出；送出後請客人交還店員", async () => {
    const calls = stubFetch(signingRoutes([task(55, "800", ["Coleman 營燈", "第 2 件"])]));
    const onDone = vi.fn();
    const user = userEvent.setup();
    wrap(<CustomerChecklist batch={ready} onDone={onDone} onClose={() => {}} />);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: /確認/ }));
    const dialog = await screen.findByRole("dialog", { name: /簽署切結書/ });
    const submit = () => within(dialog).getByRole("button", { name: /確認並送出/ }) as HTMLButtonElement;
    expect(submit().disabled).toBe(true);
    await user.click(within(dialog).getByRole("checkbox", { name: /同意/ }));
    await user.click(within(dialog).getByRole("button", { name: /購物金/ }));
    expect(submit().disabled).toBe(true);
    await user.click(within(dialog).getByRole("button", { name: "模擬簽名" }));
    expect(submit().disabled).toBe(false);
    await user.click(submit());
    await waitFor(() =>
      expect(calls.find((c) => c.url.endsWith("/signing/tasks/55/tablet-sign"))).toMatchObject({
        body: { signature_image_base64: "signature-png-base64", chosen_payout: "STORE_CREDIT" },
      }),
    );
    expect(await screen.findByText(/請把平板交還給店員/)).toBeTruthy();
    expect(onDone).toHaveBeenCalled();
  });

  it("簽署頁可以回上一頁重新勾選，再進來簽的是新內容", async () => {
    const calls = stubFetch(
      signingRoutes([task(55, "800", ["Coleman 營燈", "第 2 件"]), task(56, "300", ["Coleman 營燈"])]),
    );
    const user = userEvent.setup();
    wrap(<CustomerChecklist batch={ready} onDone={() => {}} onClose={() => {}} />);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: /確認/ }));
    let dialog = await screen.findByRole("dialog", { name: /簽署切結書/ });
    await user.click(within(dialog).getByRole("button", { name: /回上一頁/ }));
    dialog = screen.getByRole("dialog", { name: /確認要賣的商品/ });
    // 回來時保留剛才的勾選
    expect((within(dialog).getByRole("checkbox", { name: /2 號/ }) as HTMLInputElement).checked).toBe(true);
    await user.click(within(dialog).getByRole("checkbox", { name: /2 號/ }));
    await user.click(within(dialog).getByRole("button", { name: /確認/ }));
    dialog = await screen.findByRole("dialog", { name: /簽署切結書/ });
    expect(await within(dialog).findByRole("button", { name: /現金.*\$300/ })).toBeTruthy();
    const confirms = calls.filter((c) => c.url.endsWith("/customer-confirm"));
    expect(confirms.at(-1)?.body).toEqual({ kept_line_ids: [102, 103] });
    expect(calls.filter((c) => c.url.endsWith("/tablet-signature") && c.method === "POST")).toHaveLength(2);
  });

  it("簽署失敗（例如太久沒簽）：顯示原因，可回上一頁重來", async () => {
    stubFetch((url, method) => {
      if (url.endsWith("/customer-confirm")) return json(ready);
      if (url.endsWith("/tablet-signature") && method === "POST") return json(task(55, "800", ["a", "b"]));
      if (url.includes("/tablet-sign")) return json({ detail: "簽署已逾時" }, 409);
      return null;
    });
    const user = userEvent.setup();
    wrap(<CustomerChecklist batch={ready} onDone={() => {}} onClose={() => {}} />);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: /確認/ }));
    const dialog = await screen.findByRole("dialog", { name: /簽署切結書/ });
    await user.click(within(dialog).getByRole("checkbox", { name: /同意/ }));
    await user.click(within(dialog).getByRole("button", { name: /現金/ }));
    await user.click(within(dialog).getByRole("button", { name: "模擬簽名" }));
    await user.click(within(dialog).getByRole("button", { name: /確認並送出/ }));
    expect((await within(dialog).findByRole("alert")).textContent).toMatch(/逾時.*回上一頁/);
    expect(within(dialog).getByRole("button", { name: /回上一頁/ })).toBeTruthy();
  });

  it("寄售品也列在簽署頁（售價與抽成），不算進合計", async () => {
    stubFetch(
      signingRoutes([
        {
          ...task(55, "300", ["Coleman 營燈"]),
          content: {
            items: [{ name: "Coleman 營燈", amount: "300" }],
            total: "300",
            consignments: [{ name: "帳篷", listed_price: "6000", commission_pct: 50 }],
          },
        },
      ]),
    );
    const user = userEvent.setup();
    wrap(<CustomerChecklist batch={ready} onDone={() => {}} onClose={() => {}} />);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: /確認/ }));
    const dialog = await screen.findByRole("dialog", { name: /簽署切結書/ });
    const row = within(dialog).getByRole("row", { name: /帳篷/ });
    expect(row.textContent).toMatch(/寄售.*售價 \$6,000.*抽成 50%/);
    expect(within(dialog).getByText(/合計/).textContent).toContain("$300");
  });

  it("只賣寄售：不用選現金或購物金，同意＋簽名就能送出", async () => {
    const calls = stubFetch(
      signingRoutes([
        {
          ...task(57, "0", []),
          content: { items: [], total: "0", consignments: [{ name: "帳篷", listed_price: "6000", commission_pct: 50 }] },
        },
      ]),
    );
    const user = userEvent.setup();
    wrap(<CustomerChecklist batch={ready} onDone={() => {}} onClose={() => {}} />);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: /確認/ }));
    const dialog = await screen.findByRole("dialog", { name: /簽署切結書/ });
    expect(within(dialog).queryByText(/請選擇收款方式/)).toBeNull();
    expect(within(dialog).getByText(/賣出後才分帳/)).toBeTruthy();
    await user.click(within(dialog).getByRole("checkbox", { name: /同意/ }));
    await user.click(within(dialog).getByRole("button", { name: "模擬簽名" }));
    await user.click(within(dialog).getByRole("button", { name: /確認並送出/ }));
    await waitFor(() => {
      const sent = calls.find((c) => c.url.endsWith("/signing/tasks/57/tablet-sign"));
      expect(sent?.body).toMatchObject({ signature_image_base64: "signature-png-base64" });
      expect((sent?.body as Record<string, unknown>).chosen_payout ?? null).toBeNull();
    });
  });

  it("全部都不勾：出現紅色「確認都不賣」，按了整批記成不賣、請客人交還平板（店主 2026-10-03）", async () => {
    const calls = stubFetch((url) =>
      url.endsWith("/customer-decline") ? json({ ...ready, status: "CANCELLED" }) : null,
    );
    const onDone = vi.fn();
    const user = userEvent.setup();
    wrap(<CustomerChecklist batch={ready} onDone={onDone} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: /確認要賣的商品/ });
    await user.click(within(dialog).getByRole("checkbox", { name: /1 號/ }));
    await user.click(within(dialog).getByRole("checkbox", { name: /2 號/ }));
    expect(within(dialog).queryByRole("button", { name: "確認" })).toBeNull();
    const decline = within(dialog).getByRole("button", { name: "確認都不賣" });
    expect(decline.className).toContain("btn-danger");
    await user.click(decline);
    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith("/intake-batches/7/customer-decline") && c.method === "POST")).toBe(true),
    );
    expect(await screen.findByText(/這次都不賣/)).toBeTruthy();
    expect(screen.getByText(/請把平板交還給店員/)).toBeTruthy();
    expect(onDone).toHaveBeenCalled();
  });
});

describe("流程進度只剩報到收件與估價", () => {
  it("只列兩步；估完之後兩步都算完成", () => {
    wrap(<IntakeSteps status="ESTIMATING" />);
    const steps = screen.getByRole("list", { name: "流程進度" });
    expect(within(steps).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["報到收件", "估價"]);
    cleanup();
    wrap(<IntakeSteps status="AWAITING_CONFIRM" />);
    const done = screen.getByRole("list", { name: "流程進度" });
    expect(within(done).getAllByRole("listitem").every((li) => li.className === "done")).toBe(true);
  });
});
