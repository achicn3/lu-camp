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
      if (url.includes("/menu-items")) return json([]);
      return null;
    });
    renderPage();
    await waitFor(() => expect(screen.getByText(/本期不開票/)).toBeTruthy());
    expect(screen.getByText(/點下方餐飲菜單開始結帳/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "結帳" })).toHaveProperty(
      "disabled",
      true,
    );
  });

  it("點餐飲磚→數量彈窗（預設1、可加量）→加入同一購物車、總額更新", async () => {
    const MENU = [{ id: 5, store_id: 1, name: "手沖-耶加", unit_price: "180", category: "咖啡", is_available: true, sort_order: 0 }];
    stubFetch((url, method) => {
      if (url.includes("/settings")) return json(SETTINGS);
      if (url.includes("/cash-sessions/current"))
        return json({ id: 1, status: "OPEN" });
      if (url.includes("/menu-items")) return json(MENU);
      if (url.endsWith("/api/v1/sales/quote") && method === "POST") {
        return json({
          total: "360",
          campaign_id: null,
          campaign_name: null,
          lines: [],
          food_subtotal: "360",
          store_credit_max: "0",
        });
      }
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    // 點菜單磚 → 開數量彈窗
    const tile = await screen.findByRole("button", { name: /手沖-耶加/ });
    await user.click(tile);
    const dialog = await screen.findByRole("dialog", { name: /加入 手沖-耶加/ });
    // 預設數量 1 → 改成 2 → 加入購物車
    const qtyInput = within(dialog).getByLabelText("數量");
    expect(qtyInput).toHaveProperty("value", "1");
    await user.clear(qtyInput);
    await user.type(qtyInput, "2");
    await user.click(within(dialog).getByRole("button", { name: "加入購物車" }));
    // 購物車出現該行、應付總額 360（180×2，後端試算）
    await waitFor(() =>
      expect(screen.getAllByText("手沖-耶加").length).toBeGreaterThan(0),
    );
    await waitFor(() =>
      expect(screen.getAllByText(/360/).length).toBeGreaterThan(0),
    );
  });

  it("掃描序號品加入購物車、總額更新、現金結帳→完成＋列印對話框", async () => {
    let saleBody = "";
    stubFetch((url, method, body) => {
      if (url.includes("/settings")) return json(SETTINGS);
      if (url.includes("/cash-sessions/current"))
        return json({ id: 1, status: "OPEN" });
      if (url.includes("/serialized-items/by-code/TENT1")) return json(TENT);
      if (url.endsWith("/api/v1/sales/quote") && method === "POST") {
        return json({ total: "1800", campaign_id: null, campaign_name: null, lines: [], food_subtotal: "0", store_credit_max: "1800" });
      }
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
      if (url.includes("/print/detail")) return json({ status: "ok" }); // 硬體代理列印
      if (url.includes("/print-detail")) return json({ id: 7 }); // 後端稽核
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText(/本期不開票/)).toBeTruthy());

    const scan = screen.getByLabelText("掃描或輸入商品條碼");
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

  it("活動生效：總額顯示折後、結帳送折後收款、明細送印帶折扣與活動（docs/21 C2b）", async () => {
    let saleBody = "";
    let agentBody = "";
    stubFetch((url, method, body) => {
      if (url.includes("/settings")) return json(SETTINGS);
      if (url.includes("/cash-sessions/current"))
        return json({ id: 1, status: "OPEN" });
      if (url.includes("/serialized-items/by-code/TENT1")) return json(TENT);
      // 後端試算：九折 → 1800 × 0.9 = 1620（前端不自算，照單顯示/收款）。
      if (url.endsWith("/api/v1/sales/quote") && method === "POST") {
        return json({
          total: "1620",
          campaign_id: 1,
          campaign_name: "開幕九折",
          lines: [],
          food_subtotal: "0",
          store_credit_max: "1620",
        });
      }
      if (url.endsWith("/api/v1/sales") && method === "POST") {
        saleBody = body;
        return json(
          {
            id: 8,
            store_id: 1,
            total: "1620",
            total_discount: "180",
            payment_method: "CASH",
            lines: [
              {
                id: 1,
                line_type: "SERIALIZED",
                description: "雙人帳篷(測試)",
                qty: 1,
                unit_price: "1620",
                line_total: "1620",
                original_unit_price: "1800",
                discount_amount: "180",
              },
            ],
            tenders: [],
          },
          201,
        );
      }
      if (url.includes("/print/detail") && method === "POST") {
        agentBody = body; // 硬體代理收到的明細 payload
        return json({ status: "ok" });
      }
      if (url.includes("/print-detail")) return json({ id: 8 }); // 後端稽核
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText(/本期不開票/)).toBeTruthy());

    await user.type(screen.getByLabelText("掃描或輸入商品條碼"), "TENT1{Enter}");
    await waitFor(() => expect(screen.getByText("雙人帳篷(測試)")).toBeTruthy());
    // 應付總額顯示折後 1,620（非折前 1,800）
    await waitFor(() =>
      expect(screen.getAllByText(/1,620/).length).toBeGreaterThan(0),
    );
    expect(screen.getByText(/已套用活動折扣：開幕九折/)).toBeTruthy();

    const checkout = screen.getByRole("button", { name: "結帳" });
    await waitFor(() => expect(checkout).toHaveProperty("disabled", false));
    await user.click(checkout);
    await waitFor(() => expect(screen.getByText(/已完成/)).toBeTruthy());
    // 送出的現金收款為折後金額 1620（對齊後端折後 total）
    expect(JSON.parse(saleBody).tenders).toEqual([
      { tender_type: "CASH", amount: "1620" },
    ]);

    // 列印明細 → 硬體代理收到含折扣留痕 + 活動名的 SaleRead
    await user.click(screen.getByRole("button", { name: "列印明細" }));
    await waitFor(() => expect(screen.getByText(/已送出列印/)).toBeTruthy());
    const printed = JSON.parse(agentBody);
    expect(printed.campaign_name).toBe("開幕九折");
    expect(printed.total_discount).toBe("180");
    expect(printed.lines[0].discount_amount).toBe("180");
    expect(printed.lines[0].original_unit_price).toBe("1800");
  });

  it("二手＋餐飲＋會員選購物金：顯示「內用餐飲不可用購物金折抵」上限訊息並停用結帳（回歸）", async () => {
    // 回歸：TenderPanel 過去自算 validatePlan 卻漏傳 storeCreditMax，導致按鈕被停用卻不顯示原因。
    const MEMBER = {
      id: 7,
      store_id: 1,
      name: "林測試",
      phone: "0900000000",
      roles: ["MEMBER"],
      member_points: 0,
      national_id_masked: null,
    };
    stubFetch((url, method) => {
      if (url.includes("/settings")) return json(SETTINGS);
      if (url.includes("/cash-sessions/current"))
        return json({ id: 1, status: "OPEN" });
      if (url.includes("/serialized-items/by-code/TENT1")) return json(TENT);
      if (url.includes("/contacts/7/store-credit"))
        return json({ contact_id: 7, balance: "5000" });
      if (url.includes("/api/v1/contacts") && method === "GET")
        return json([MEMBER]);
      if (url.endsWith("/api/v1/sales/quote") && method === "POST") {
        // total=1920（二手1800+餐飲120）、餐飲小計120 → 購物金上限=1800。
        return json({
          total: "1920",
          campaign_id: null,
          campaign_name: null,
          lines: [],
          food_subtotal: "120",
          store_credit_max: "1800",
        });
      }
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText(/本期不開票/)).toBeTruthy());
    // 二手序號品入車
    await user.type(screen.getByLabelText("掃描或輸入商品條碼"), "TENT1{Enter}");
    await waitFor(() => expect(screen.getByText("雙人帳篷(測試)")).toBeTruthy());
    // 歸戶會員
    await user.type(screen.getByPlaceholderText("姓名或電話"), "林測試");
    await user.click(screen.getByRole("button", { name: "查詢會員" }));
    await user.click(await screen.findByRole("button", { name: /林測試/ }));
    await waitFor(() => expect(screen.getByText(/購物金餘額/)).toBeTruthy());
    // 選購物金折抵 → 顯示可折抵上限與餘額
    await user.click(screen.getByText("購物金折抵"));
    await waitFor(() =>
      expect(screen.getByText(/購物金可折抵/)).toBeTruthy(),
    );
    // 輸入超過可折抵上限（1800）的金額 → 出現上限訊息、結帳停用
    await user.type(screen.getByLabelText("購物金折抵金額"), "1900");
    await waitFor(() =>
      expect(screen.getByText(/內用餐飲不可用購物金折抵（購物金最多 1800 元）/)).toBeTruthy(),
    );
    expect(screen.getByRole("button", { name: "結帳" })).toHaveProperty(
      "disabled",
      true,
    );
  });

  it("二手＋會員選購物金折抵：全額折抵帶入餘額/上限較小者，結帳啟用（純購物金付款）", async () => {
    const MEMBER = {
      id: 7,
      store_id: 1,
      name: "林測試",
      phone: "0900000000",
      roles: ["MEMBER"],
      member_points: 0,
      national_id_masked: null,
    };
    stubFetch((url, method) => {
      if (url.includes("/settings")) return json(SETTINGS);
      if (url.includes("/cash-sessions/current"))
        return json({ id: 1, status: "OPEN" });
      if (url.includes("/serialized-items/by-code/TENT1")) return json(TENT);
      if (url.includes("/contacts/7/store-credit"))
        return json({ contact_id: 7, balance: "5000" });
      if (url.includes("/api/v1/contacts") && method === "GET")
        return json([MEMBER]);
      if (url.endsWith("/api/v1/sales/quote") && method === "POST") {
        // total=1800（純二手）、無餐飲 → 購物金上限=1800。
        return json({
          total: "1800",
          campaign_id: null,
          campaign_name: null,
          lines: [],
          food_subtotal: "0",
          store_credit_max: "1800",
        });
      }
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText(/本期不開票/)).toBeTruthy());
    await user.type(screen.getByLabelText("掃描或輸入商品條碼"), "TENT1{Enter}");
    await waitFor(() => expect(screen.getByText("雙人帳篷(測試)")).toBeTruthy());
    await user.type(screen.getByPlaceholderText("姓名或電話"), "林測試");
    await user.click(screen.getByRole("button", { name: "查詢會員" }));
    await user.click(await screen.findByRole("button", { name: /林測試/ }));
    await waitFor(() => expect(screen.getByText(/購物金餘額/)).toBeTruthy());
    await user.click(screen.getByText("購物金折抵"));
    // 全額折抵 → 帶入 min(餘額5000, 上限1800, 應付1800)=1800，現金腿 0 → 結帳啟用
    await user.click(screen.getByRole("button", { name: "全額折抵" }));
    await waitFor(() =>
      expect(screen.getByLabelText("購物金折抵金額")).toHaveProperty(
        "value",
        "1800",
      ),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "結帳" })).toHaveProperty(
        "disabled",
        false,
      ),
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

  it("掃到完整碼制自動加入購物車（免按 Enter）", async () => {
    stubFetch((url) => {
      if (url.includes("/settings")) return json(SETTINGS);
      if (url.includes("/cash-sessions/current"))
        return json({ id: 1, status: "OPEN" });
      if (url.includes("/serialized-items/by-code/S1-ABCDEF0123"))
        return json({ ...TENT, item_code: "S1-ABCDEF0123" });
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText(/本期不開票/)).toBeTruthy());
    // 不打 {Enter}：輸入到符合碼制即自動送出加入購物車
    await user.type(screen.getByLabelText("掃描或輸入商品條碼"), "S1-ABCDEF0123");
    await waitFor(() =>
      expect(screen.getByText("雙人帳篷(測試)")).toBeTruthy(),
    );
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
    await user.type(screen.getByLabelText("掃描或輸入商品條碼"), "NOPE{Enter}");
    await waitFor(() =>
      expect(screen.getByText(/找不到此條碼：NOPE/)).toBeTruthy(),
    );
  });
});
