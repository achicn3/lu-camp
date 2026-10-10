// @vitest-environment jsdom
// 庫存「販售籃」從現有散裝開籃／加入（店主 2026-10-10：豬尾巴後來想共用標籤，不必再透過收購頁）。
// 限管理者；同價才能放同一籃（2026-09-22 裁示），售價不同的散裝不能勾並講清楚。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BasketPanel } from "@/features/inventory/BasketPanel";
import { setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function lot(over: Record<string, unknown>) {
  return {
    id: 1,
    store_id: 1,
    lot_code: "L1-9DC988B631",
    label: null,
    name: "豬尾巴",
    brand_id: 3,
    category_id: 4,
    grade: "E",
    acquisition_cost: "100",
    acquisition_basis: "UNSPECIFIED",
    unit_price: "29",
    retail_price: null,
    total_qty: 10,
    remaining_qty: 7,
    status: "ON_SALE",
    note: null,
    basket_id: null,
    ...over,
  };
}

const LOTS = [
  lot({}),
  lot({ id: 2, lot_code: "L1-2222222222", remaining_qty: 5 }),
  lot({ id: 3, lot_code: "L1-3333333333", unit_price: "35" }),
  lot({ id: 4, lot_code: "L1-4444444444", basket_id: 9 }), // 已在別的籃
  lot({ id: 5, lot_code: "L1-5555555555", status: "WRITTEN_OFF" }), // 收購已作廢
];

const BASKET = {
  id: 5,
  store_id: 1,
  code: "K1-ABCDEF0123",
  name: "豬尾巴",
  brand_id: 3,
  category_id: 4,
  unit_price: "29",
  note: null,
  is_active: true,
  remaining_qty: 7,
  sources: [],
  cost_reference: { sample_count: 0, unit_cost_min: null, unit_cost_max: null },
};

type Call = { url: string; method: string; body: unknown };
let calls: Call[] = [];

function stub(baskets: unknown[] = [], postStatus = 201) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const request = input as Request;
      const body = request.method === "GET" ? null : await request.clone().json();
      calls.push({ url: request.url, method: request.method, body });
      if (request.url.includes("/bulk-lots")) return json(LOTS);
      if (request.method === "POST" && request.url.endsWith("/bulk-baskets")) {
        return postStatus === 201
          ? json({ ...BASKET, id: 6, code: "K1-NEW0000001" }, 201)
          : json({ detail: "售價不同（這批 35 元、籃子 29 元），同價才能放同一籃" }, postStatus);
      }
      if (request.method === "POST" && request.url.includes("/bulk-baskets/5/lots")) {
        return json({ ...BASKET, remaining_qty: 12 });
      }
      if (request.url.includes("/bulk-baskets")) return json(baskets);
      return json([]);
    }),
  );
}

function renderPanel(role: "MANAGER" | "CLERK") {
  setToken(fakeJwt({ sub: "1", role, store_id: 1 }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<BasketPanel />, { wrapper });
}

async function searchLots(scope: HTMLElement) {
  await userEvent.type(within(scope).getByLabelText("搜尋散裝"), "豬尾巴");
  await userEvent.click(within(scope).getByRole("button", { name: "找散裝" }));
  await within(scope).findByText("L1-9DC988B631");
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("販售籃：從現有散裝開籃／加入", () => {
  it("店員看不到「開新販售籃」與「加入現有散裝」", async () => {
    stub([BASKET]);
    renderPanel("CLERK");
    await screen.findByText("K1-ABCDEF0123");
    expect(screen.queryByRole("button", { name: "開新販售籃" })).toBeNull();
    expect(screen.queryByRole("button", { name: "加入現有散裝" })).toBeNull();
  });

  it("開新販售籃：勾現有散裝、名稱與售價帶第一筆，一次送出", async () => {
    stub();
    renderPanel("MANAGER");
    await userEvent.click(await screen.findByRole("button", { name: "開新販售籃" }));
    const form = screen.getByRole("form", { name: "開新販售籃" });
    await searchLots(form);
    // 已在別籃、收購已作廢的不列
    expect(within(form).queryByText("L1-4444444444")).toBeNull();
    expect(within(form).queryByText("L1-5555555555")).toBeNull();
    await userEvent.click(within(form).getByRole("checkbox", { name: /L1-9DC988B631/ }));
    expect((within(form).getByLabelText("販售籃名稱") as HTMLInputElement).value).toBe("豬尾巴");
    expect((within(form).getByLabelText("每件售價") as HTMLInputElement).value).toBe("29");
    await userEvent.click(within(form).getByRole("checkbox", { name: /L1-2222222222/ }));
    // 售價不同的不能勾，並講出它的價錢
    const pricier = within(form).getByRole("checkbox", { name: /L1-3333333333/ }) as HTMLInputElement;
    expect(pricier.disabled).toBe(true);
    expect(within(form).getByText(/售價 35 元不同/)).toBeTruthy();

    await userEvent.click(within(form).getByRole("button", { name: "建立販售籃" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      name: "豬尾巴",
      unit_price: "29",
      brand_id: 3,
      category_id: 4,
      bulk_lot_ids: [1, 2],
    });
    expect(await screen.findByText(/已開販售籃「豬尾巴」.*請印籃子標籤/)).toBeTruthy();
  });

  it("開新販售籃：沒勾任何散裝不能建立", async () => {
    stub();
    renderPanel("MANAGER");
    await userEvent.click(await screen.findByRole("button", { name: "開新販售籃" }));
    const form = screen.getByRole("form", { name: "開新販售籃" });
    expect(
      (within(form).getByRole("button", { name: "建立販售籃" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("開新販售籃：後端擋下時顯示原因", async () => {
    stub([], 409);
    renderPanel("MANAGER");
    await userEvent.click(await screen.findByRole("button", { name: "開新販售籃" }));
    const form = screen.getByRole("form", { name: "開新販售籃" });
    await searchLots(form);
    await userEvent.click(within(form).getByRole("checkbox", { name: /L1-9DC988B631/ }));
    await userEvent.click(within(form).getByRole("button", { name: "建立販售籃" }));
    expect(await within(form).findByRole("alert")).toHaveProperty(
      "textContent",
      "售價不同（這批 35 元、籃子 29 元），同價才能放同一籃",
    );
  });

  it("籃子「加入現有散裝」：只能勾同價的，逐筆加入後提示", async () => {
    stub([BASKET]);
    renderPanel("MANAGER");
    await userEvent.click(await screen.findByRole("button", { name: "加入現有散裝" }));
    const panel = screen.getByRole("form", { name: "豬尾巴 加入現有散裝" });
    await searchLots(panel);
    expect(
      (within(panel).getByRole("checkbox", { name: /L1-3333333333/ }) as HTMLInputElement).disabled,
    ).toBe(true);
    await userEvent.click(within(panel).getByRole("checkbox", { name: /L1-9DC988B631/ }));
    await userEvent.click(within(panel).getByRole("checkbox", { name: /L1-2222222222/ }));
    await userEvent.click(within(panel).getByRole("button", { name: "加入這 2 筆" }));

    await waitFor(() =>
      expect(calls.filter((c) => c.method === "POST").map((c) => c.body)).toEqual([
        { bulk_lot_id: 1 },
        { bulk_lot_id: 2 },
      ]),
    );
    expect(await screen.findByText(/已把 2 筆散裝加入「豬尾巴」/)).toBeTruthy();
  });
});
