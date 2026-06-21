// @vitest-environment jsdom
// /pos 結帳頁測試：空車禁結帳、掃描序號品加入、現金結帳→完成＋列印對話框、發票區隱藏。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import PosPage from "@/app/(authed)/pos/page";
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

type FetchRoute = (
  url: string,
  method: string,
  body: string,
) => Response | null;

function stubFetch(route: FetchRoute) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method =
        (input instanceof Request ? input.method : init?.method) ?? "GET";
      const body =
        input instanceof Request
          ? await input.clone().text()
          : String(init?.body ?? "");
      const resp = route(url, method, body);
      if (resp) return resp;
      throw new Error(`unmatched fetch: ${method} ${url}`);
    }),
  );
}

function renderPage() {
  setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<PosPage />, { wrapper: Wrapper });
}

const SETTINGS = {
  store_id: 1,
  einvoice_enabled: false,
  tax_rate: "0.05",
  default_commission_pct: 50,
  default_margin_pct: 45,
  premium_rate: "0.10",
};

const TENT = {
  id: 1,
  item_code: "TENT1",
  name: "雙人帳篷(測試)",
  grade: "A",
  listed_price: "1800",
  status: "IN_STOCK",
  brand_id: null,
  product_model_id: null,
  consignor_id: null,
  commission_pct: null,
  ownership_type: "OWNED",
  intake_date: "2026-06-13T00:00:00Z",
  sold_date: null,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("/pos 結帳頁", () => {
  it("空車：顯示提示、發票區標示本期不開票、結帳鍵停用", async () => {
    stubFetch((url) => {
      if (url.includes("/settings")) return json(SETTINGS);
      if (url.includes("/cash-sessions/current"))
        return json({ id: 1, status: "OPEN" });
      return null;
    });
    renderPage();
    await waitFor(() => expect(screen.getByText(/本期不開票/)).toBeTruthy());
    expect(screen.getByText(/掃描或輸入商品條碼開始結帳/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "結帳" })).toHaveProperty(
      "disabled",
      true,
    );
  });

  it("掃描序號品加入購物車、總額更新、現金結帳→完成＋列印對話框", async () => {
    let saleBody = "";
    stubFetch((url, method, body) => {
      if (url.includes("/settings")) return json(SETTINGS);
      if (url.includes("/cash-sessions/current"))
        return json({ id: 1, status: "OPEN" });
      if (url.includes("/serialized-items/by-code/TENT1")) return json(TENT);
      if (url.endsWith("/api/v1/sales") && method === "POST") {
        saleBody = body;
        return json(
          {
            id: 7,
            store_id: 1,
            total: "1800",
            payment_method: "CASH",
            lines: [],
            tenders: [],
          },
          201,
        );
      }
      if (url.includes("/print-detail")) return json({ id: 7 });
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText(/本期不開票/)).toBeTruthy());

    const scan = screen.getByLabelText("掃描或輸入條碼");
    await user.type(scan, "TENT1{Enter}");
    await waitFor(() =>
      expect(screen.getByText("雙人帳篷(測試)")).toBeTruthy(),
    );
    // 應付總額顯示 1,800
    expect(screen.getAllByText(/1,800/).length).toBeGreaterThan(0);

    const checkout = screen.getByRole("button", { name: "結帳" });
    expect(checkout).toHaveProperty("disabled", false);
    await user.click(checkout);

    // 完成畫面 + 列印對話框
    await waitFor(() => expect(screen.getByText(/已完成/)).toBeTruthy());
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/列印商品明細？/)).toBeTruthy();
    // 送出的 tenders 為單一現金全額
    expect(JSON.parse(saleBody).tenders).toEqual([
      { tender_type: "CASH", amount: "1800" },
    ]);

    // 列印明細 → 呼叫 print-detail
    await user.click(within(dialog).getByRole("button", { name: "列印明細" }));
    await waitFor(() =>
      expect(within(dialog).getByText(/已送出列印/)).toBeTruthy(),
    );
  });

  it("生效活動橫幅顯示活動名稱與折扣", async () => {
    const ACTIVE_CAMPAIGN = {
      id: 1,
      store_id: 1,
      name: "開幕九折",
      discount_pct: 10,
      starts_at: "2026-06-20T00:00:00Z",
      ends_at: "2026-06-30T23:59:59Z",
      status: "ACTIVE",
      applies_owned_serialized: true,
      applies_owned_bulk: true,
      applies_catalog: false,
      applies_consignment: false,
      consignment_discount_bearing: "STORE_ABSORBS",
      created_by: 1,
      created_at: "2026-06-19T10:00:00Z",
      updated_at: "2026-06-19T10:00:00Z",
    };
    stubFetch((url) => {
      if (url.includes("/settings")) return json(SETTINGS);
      if (url.includes("/cash-sessions/current"))
        return json({ id: 1, status: "OPEN" });
      if (url.includes("/campaigns")) return json([ACTIVE_CAMPAIGN]);
      return null;
    });
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/開幕九折/)).toBeTruthy(),
    );
    expect(screen.getByText(/9 折/)).toBeTruthy();
    expect(screen.getByText(/結帳會自動套用折扣/)).toBeTruthy();
  });

  it("掃到不存在的條碼顯示錯誤", async () => {
    stubFetch((url) => {
      if (url.includes("/settings")) return json(SETTINGS);
      if (url.includes("/cash-sessions/current"))
        return json({ id: 1, status: "OPEN" });
      if (url.includes("/serialized-items/by-code/"))
        return json({ detail: "not found" }, 404);
      if (url.includes("/bulk-lots/by-code/"))
        return json({ detail: "not found" }, 404);
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText(/本期不開票/)).toBeTruthy());
    await user.type(screen.getByLabelText("掃描或輸入條碼"), "NOPE{Enter}");
    await waitFor(() =>
      expect(screen.getByText(/找不到此條碼：NOPE/)).toBeTruthy(),
    );
  });
});
