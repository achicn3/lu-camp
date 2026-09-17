// @vitest-environment jsdom
// /menu 餐飲菜單管理頁測試：清單渲染、建立、上下架切換、MANAGER 權限閘。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import MenuPage from "@/app/(authed)/menu/page";
import { clearToken, setToken } from "@/lib/token";

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

const ITEMS = [
  { id: 1, store_id: 1, name: "手沖-耶加", unit_price: "180", unit_cost: "60", category: "咖啡", is_available: true, sort_order: 0 },
  { id: 2, store_id: 1, name: "季節限定", unit_price: "200", unit_cost: null, category: null, is_available: false, sort_order: 1 },
];

// 建議售價要用店內設定的稅率/手續費，不可寫死（CLAUDE.md §7.9）。
const SETTINGS = {
  tax_rate: "0.05",
  linepay_fee_pct: "0.022",
  taiwanpay_fee_pct: "0",
  purchase_default_margin_pct: 30,
};

type Route = (url: string, method: string, body: string) => Response | null;

function stubFetch(route: Route, settings: Record<string, unknown> = SETTINGS) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      const body =
        input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
      // 角色以 DB 現值為準（menu 頁 gate 改用 useCurrentRole）：測試一律回 MANAGER。
      if (url.includes("/auth/me")) return json({ id: 1, role: "MANAGER", store_id: 1 });
      if (url.includes("/settings")) return json(settings);
      const resp = route(url, method, body);
      if (resp) return resp;
      throw new Error(`unmatched fetch: ${method} ${url}`);
    }),
  );
}

function renderPage(role: "MANAGER" | "CLERK" = "MANAGER") {
  setToken(fakeJwt({ sub: "1", role, store_id: 1 }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<MenuPage />, { wrapper: Wrapper });
}

afterEach(() => {
  cleanup();
  clearToken();
  vi.unstubAllGlobals();
});

describe("/menu 餐飲菜單管理頁", () => {
  it("CLERK 無權限：顯示需管理者權限", () => {
    stubFetch(() => json([]));
    renderPage("CLERK");
    expect(screen.getByText("需管理者權限")).toBeTruthy();
  });

  it("MANAGER：清單渲染品名/售價/狀態（含停售）", async () => {
    stubFetch((url) => (url.includes("/menu-items") ? json(ITEMS) : null));
    renderPage("MANAGER");
    expect(await screen.findByText("手沖-耶加")).toBeTruthy();
    expect(screen.getByText("可售", { selector: ".inv-badge" })).toBeTruthy();
    expect(screen.getByText("停售", { selector: ".inv-badge" })).toBeTruthy();
  });

  it("建立品項：POST 後刷新清單", async () => {
    let posted = "";
    stubFetch((url, method, body) => {
      if (url.includes("/menu-items") && method === "POST") {
        posted = body;
        return json({ id: 9, store_id: 1, name: "拿鐵", unit_price: "150", category: "咖啡", is_available: true, sort_order: 0 }, 201);
      }
      if (url.includes("/menu-items")) return json(ITEMS);
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    await screen.findByText("手沖-耶加");
    await user.type(screen.getByLabelText("品名"), "拿鐵");
    await user.type(screen.getByLabelText("售價（整數元）"), "150");
    await user.click(screen.getByRole("button", { name: "新增品項" }));
    await waitFor(() => expect(posted).toContain("拿鐵"));
    expect(JSON.parse(posted).unit_price).toBe("150");
  });

  it("下架：PATCH is_available=false", async () => {
    let patched = "";
    stubFetch((url, method, body) => {
      if (url.includes("/menu-items/1") && method === "PATCH") {
        patched = body;
        return json({ ...ITEMS[0], is_available: false });
      }
      if (url.includes("/menu-items")) return json(ITEMS);
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    await screen.findByText("手沖-耶加");
    // 第一列（可售）的「下架」鈕
    await user.click(screen.getAllByRole("button", { name: "下架" })[0]);
    await waitFor(() => expect(patched).toContain("is_available"));
    expect(JSON.parse(patched).is_available).toBe(false);
  });

  it("清單顯示成本與毛利率；沒填成本顯示未填", async () => {
    stubFetch((url) => (url.includes("/menu-items") ? json(ITEMS) : null));
    renderPage("MANAGER");
    expect(await screen.findByText("手沖-耶加")).toBeTruthy();
    // 欄位順序：品名｜分類｜售價｜成本｜預估毛利率｜狀態｜操作
    const row = screen.getByText("手沖-耶加").closest("tr")!;
    expect(row.cells[3].textContent).toContain("60");
    // 售價 180 含稅、成本 60：未稅實得 171 − 手續費 4 = 167 → 毛利率 (167−60)/167 = 64%
    expect(row.cells[4].textContent).toBe("64%");
    const noCost = screen.getByText("季節限定").closest("tr")!;
    expect(noCost.cells[3].textContent).toContain("未填");
    expect(noCost.cells[4].textContent).toBe("—");  // 成本未知就不編造毛利率
  });

  it("建立品項：填成本＋毛利率自動帶出建議售價，並把成本一起送出", async () => {
    let posted = "";
    stubFetch((url, method, body) => {
      if (url.includes("/menu-items") && method === "POST") {
        posted = body;
        return json({ id: 9, store_id: 1, name: "拿鐵", unit_price: "191", unit_cost: "60", category: null, is_available: true, sort_order: 0 }, 201);
      }
      if (url.includes("/menu-items")) return json(ITEMS);
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    await screen.findByText("手沖-耶加");
    await user.type(screen.getByLabelText("品名"), "拿鐵");
    await user.type(screen.getByLabelText("成本（整數元，選填）"), "60");
    // 預設毛利率 30%：未稅 60÷0.7=85.71 → 含稅 ÷(1−0.022×1.05) → 92
    await waitFor(() => expect((screen.getByLabelText("售價（整數元）") as HTMLInputElement).value).toBe("92"));
    await user.click(screen.getByRole("button", { name: "新增品項" }));
    await waitFor(() => expect(posted).toContain("拿鐵"));
    expect(JSON.parse(posted).unit_cost).toBe("60");
    expect(JSON.parse(posted).unit_price).toBe("92");
  });

  it("讀不到手續費率時仍算建議售價（費率以 0 計，CLAUDE.md §7.9）", async () => {
    stubFetch((url) => (url.includes("/menu-items") ? json(ITEMS) : null), {
      ...SETTINGS,
      linepay_fee_pct: null,
      taiwanpay_fee_pct: null,
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    await screen.findByText("手沖-耶加");
    await user.type(screen.getByLabelText("成本（整數元，選填）"), "60");
    // 費率 0：60÷0.7=85.71 → ×1.05 = 90（少補手續費，不把客人的價格墊高）
    await waitFor(() =>
      expect((screen.getByLabelText("售價（整數元）") as HTMLInputElement).value).toBe("90"),
    );
    // 但既有品項的預估毛利率不可用 0 費率硬算（會高估店家收益）
    const row = screen.getByText("手沖-耶加").closest("tr")!;
    expect(row.cells[4].textContent).toBe("—");
  });

  it("沒填成本就不送 unit_cost（留 null＝成本未知，不可當 0）", async () => {
    let posted = "";
    stubFetch((url, method, body) => {
      if (url.includes("/menu-items") && method === "POST") {
        posted = body;
        return json({ id: 9, store_id: 1, name: "白開水", unit_price: "10", unit_cost: null, category: null, is_available: true, sort_order: 0 }, 201);
      }
      if (url.includes("/menu-items")) return json(ITEMS);
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    await screen.findByText("手沖-耶加");
    await user.type(screen.getByLabelText("品名"), "白開水");
    await user.type(screen.getByLabelText("售價（整數元）"), "10");
    await user.click(screen.getByRole("button", { name: "新增品項" }));
    await waitFor(() => expect(posted).toContain("白開水"));
    expect(JSON.parse(posted).unit_cost).toBe(null);
  });

  it("改成本：PATCH unit_cost", async () => {
    let patched = "";
    stubFetch((url, method, body) => {
      if (url.includes("/menu-items/1") && method === "PATCH") {
        patched = body;
        return json({ ...ITEMS[0], unit_cost: "70" });
      }
      if (url.includes("/menu-items")) return json(ITEMS);
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    await screen.findByText("手沖-耶加");
    await user.click(screen.getAllByRole("button", { name: "改成本" })[0]);
    const input = screen.getByLabelText("手沖-耶加 成本");
    await user.clear(input);
    await user.type(input, "70");
    await user.click(screen.getAllByRole("button", { name: "儲存" })[0]);
    await waitFor(() => expect(patched).toContain("unit_cost"));
    expect(JSON.parse(patched).unit_cost).toBe("70");
  });

  it("清空成本欄位＝把成本改回未知（送 null）", async () => {
    let patched = "";
    stubFetch((url, method, body) => {
      if (url.includes("/menu-items/1") && method === "PATCH") {
        patched = body;
        return json({ ...ITEMS[0], unit_cost: null });
      }
      if (url.includes("/menu-items")) return json(ITEMS);
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    await screen.findByText("手沖-耶加");
    await user.click(screen.getAllByRole("button", { name: "改成本" })[0]);
    await user.clear(screen.getByLabelText("手沖-耶加 成本"));
    await user.click(screen.getAllByRole("button", { name: "儲存" })[0]);
    await waitFor(() => expect(patched).toContain("unit_cost"));
    expect(JSON.parse(patched).unit_cost).toBe(null);
  });
});
