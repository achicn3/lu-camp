// @vitest-environment jsdom
// /menu 餐飲菜單管理頁測試：清單渲染、建立、上下架切換、MANAGER 權限閘。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
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
  { id: 1, store_id: 1, name: "手沖-耶加", unit_price: "180", unit_cost: "60", category: "咖啡", is_available: true, sort_order: 0, option_groups: [] },
  { id: 2, store_id: 1, name: "季節限定", unit_price: "200", unit_cost: null, category: null, is_available: false, sort_order: 1, option_groups: [] },
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
  it("刪除分類最後一項後仍明確保留所選篩選", async () => {
    let items = [...ITEMS];
    stubFetch((url, method) => {
      if (url.includes("/menu-items/1/delete") && method === "DELETE") {
        items = items.filter((item) => item.id !== 1);
        return new Response(null, { status: 204 });
      }
      if (url.includes("/menu-items")) return json(items);
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("手沖-耶加");
    await user.selectOptions(screen.getByLabelText("品項分類"), "category:咖啡");
    await user.click(screen.getByRole("button", { name: "刪除" }));
    const dialog = await screen.findByRole("dialog", { name: "刪除品項" });
    await user.click(within(dialog).getByRole("button", { name: "刪除" }));
    await screen.findByText("沒有符合條件的品項，請調整或清除篩選。");
    expect((screen.getByLabelText("品項分類") as HTMLSelectElement).value).toBe("category:咖啡");
    await user.click(screen.getByRole("button", { name: "清除篩選" }));
    expect(screen.getByText("季節限定").closest("tr")?.hidden).toBe(false);
  });

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

  it("預設只顯示品項；四個功能分開且切換保留新增草稿", async () => {
    stubFetch((url) => {
      if (url.includes("/menu-items")) return json(ITEMS);
      if (url.includes("/menu-option-groups")) return json([]);
      if (url.includes("/online-order/status")) return json({ configured: false, tables: [] });
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("手沖-耶加");
    const itemsTab = screen.getByRole("tab", { name: "品項" });
    expect(itemsTab.getAttribute("aria-selected")).toBe("true");
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "品項", "選項群組", "分類與排序", "線上發布",
    ]);
    expect(screen.queryByRole("region", { name: "線上點餐" })).toBeNull();
    expect(screen.queryByRole("form", { name: "新增選項群組" })).toBeNull();
    expect(screen.getByLabelText("品名").closest("[hidden]")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "新增品項" }));
    await user.type(screen.getByLabelText("品名"), "還沒完成的拿鐵");
    await user.click(screen.getByRole("tab", { name: "選項群組" }));
    expect(screen.getByRole("form", { name: "新增選項群組" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "手沖-耶加 編輯" })).toBeNull();
    await user.type(screen.getByLabelText("群組名稱"), "還沒完成的溫度");
    await user.click(screen.getByRole("tab", { name: "線上發布" }));
    expect(screen.getByRole("region", { name: "線上點餐" })).toBeTruthy();
    await user.click(itemsTab);
    expect((screen.getByLabelText("品名") as HTMLInputElement).value).toBe("還沒完成的拿鐵");
    await user.click(screen.getByRole("button", { name: "收起新增品項" }));
    await user.click(screen.getByRole("button", { name: "新增品項" }));
    expect((screen.getByLabelText("品名") as HTMLInputElement).value).toBe("還沒完成的拿鐵");
    await user.click(screen.getByRole("tab", { name: "選項群組" }));
    expect((screen.getByLabelText("群組名稱") as HTMLInputElement).value).toBe("還沒完成的溫度");
  });

  it("搜尋、分類、販售狀態交集篩選；清除後恢復全部，不寫入資料", async () => {
    const writes: string[] = [];
    const items = [
      ...ITEMS,
      { ...ITEMS[0], id: 3, name: "Coffee 冰拿鐵", category: "咖啡" },
      { ...ITEMS[0], id: 4, name: "Coffee 蛋糕", category: "甜點", is_available: false },
    ];
    stubFetch((url, method) => {
      if (method !== "GET") writes.push(url);
      if (url.includes("/menu-items")) return json(items);
      if (url.includes("/menu-option-groups")) return json([]);
      if (url.includes("/online-order/status")) return json({ configured: false, tables: [] });
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    const panel = screen.getByRole("tabpanel", { name: "品項" });
    const table = within(panel).getByRole("table");
    await within(table).findByText("Coffee 冰拿鐵");
    await user.type(screen.getByRole("searchbox", { name: "搜尋品名" }), "  coffee  ");
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    await user.selectOptions(screen.getByLabelText("品項分類"), "category:咖啡");
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    await user.selectOptions(screen.getByLabelText("販售狀態"), "unavailable");
    expect(within(table).getAllByRole("row")).toHaveLength(1);
    expect(screen.getByText("沒有符合條件的品項，請調整或清除篩選。")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "清除篩選" }));
    expect(within(table).getAllByRole("row")).toHaveLength(5);
    await user.selectOptions(screen.getByLabelText("品項分類"), "uncategorized");
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(within(table).getByRole("button", { name: "季節限定 編輯" })).toBeTruthy();
    await user.click(screen.getByRole("tab", { name: "線上發布" }));
    await user.click(screen.getByRole("tab", { name: "品項" }));
    expect((screen.getByLabelText("品項分類") as HTMLSelectElement).value).toBe("uncategorized");
    expect(writes).toEqual([]);
  });

  it("鍵盤可用方向鍵與 Home/End 切換功能；只讓選中分頁進入 Tab 順序", async () => {
    stubFetch((url) => {
      if (url.includes("/menu-items")) return json(ITEMS);
      if (url.includes("/menu-option-groups")) return json([]);
      if (url.includes("/online-order/status")) return json({ configured: false, tables: [] });
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("手沖-耶加");
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((tab) => tab.tabIndex)).toEqual([0, -1, -1, -1]);
    tabs[0].focus();
    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(tabs[1]);
    expect(screen.getByRole("tabpanel", { name: "選項群組" })).toBeTruthy();
    await user.keyboard("{End}");
    expect(document.activeElement).toBe(tabs[3]);
    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(tabs[0]);
    await user.keyboard("{ArrowLeft}");
    expect(document.activeElement).toBe(tabs[3]);
    await user.keyboard("{Home}");
    expect(tabs.map((tab) => tab.tabIndex)).toEqual([0, -1, -1, -1]);
  });

  it("選項欄列出掛的群組；下方有選項群組管理；點編輯開品項編輯（含選項）", async () => {
    const group = { id: 3, name: "溫度", min_select: 1, max_select: 1, sort_order: 0, options: [] };
    stubFetch((url) => {
      if (url.includes("/menu-items")) return json([{ ...ITEMS[0], option_groups: [group] }, ITEMS[1]]);
      if (url.includes("/menu-option-groups")) return json([group]);
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    expect(await screen.findByText("沒有選項")).toBeTruthy();
    await user.click(screen.getByRole("tab", { name: "選項群組" }));
    expect(await screen.findByRole("region", { name: "溫度" })).toBeTruthy();
    await user.click(screen.getByRole("tab", { name: "品項" }));
    await user.click(screen.getByRole("button", { name: "手沖-耶加 編輯" }));
    const dialog = await screen.findByRole("dialog", { name: "編輯 手沖-耶加" });
    expect(
      (await within(dialog).findByRole("checkbox", { name: /溫度/ }) as HTMLInputElement).checked,
    ).toBe(true);
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
    await user.click(screen.getByRole("button", { name: "新增品項" }));
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

  it("勾「每日限量」：PATCH daily_limited=true；已限量的顯示今天剩幾份", async () => {
    let patched = "";
    const items = [
      ITEMS[0],
      { ...ITEMS[1], name: "戚風", is_available: true, daily_limited: true, remaining: 3, stock_set_today: true },
    ];
    stubFetch((url, method, body) => {
      if (url.includes("/menu-items/1") && method === "PATCH") {
        patched = body;
        return json({ ...ITEMS[0], daily_limited: true, remaining: 0 });
      }
      if (url.includes("/menu-items")) return json(items);
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    await screen.findByText("手沖-耶加");
    expect(screen.getByText("今天剩 3 份")).toBeTruthy();
    await user.click(screen.getByLabelText("手沖-耶加 每日限量"));
    await waitFor(() => expect(patched).not.toBe(""));
    expect(JSON.parse(patched)).toEqual({ daily_limited: true });
  });

  it("清單顯示成本與毛利率；沒填成本顯示未填", async () => {
    stubFetch((url) => (url.includes("/menu-items") ? json(ITEMS) : null));
    renderPage("MANAGER");
    expect(await screen.findByText("手沖-耶加")).toBeTruthy();
    // 欄位順序：照片｜品名｜分類｜售價｜成本｜預估毛利率｜狀態｜操作
    const row = screen.getByText("手沖-耶加").closest("tr")!;
    expect(row.cells[4].textContent).toContain("60");
    // 售價 180 含稅、成本 60：未稅實得 171 − 手續費 4 = 167 → 毛利率 (167−60)/167 = 64%
    expect(row.cells[5].textContent).toBe("64%");
    const noCost = screen.getByText("季節限定").closest("tr")!;
    expect(noCost.cells[4].textContent).toContain("未填");
    expect(noCost.cells[5].textContent).toBe("—");  // 成本未知就不編造毛利率
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
    await user.click(screen.getByRole("button", { name: "新增品項" }));
    await user.type(screen.getByLabelText("品名"), "拿鐵");
    await user.type(screen.getByLabelText("成本（整數元，選填）"), "60");
    // 預設毛利率 30%：未稅 60÷0.7=85.71 → 含稅 ÷(1−0.022×1.05) → 92 → 進位 → 100
    // 餐飲售價也走同一條進位（ADR-023）：菜單板一樣要好讀、找零一樣要備一元硬幣。
    await waitFor(() => expect((screen.getByLabelText("售價（整數元）") as HTMLInputElement).value).toBe("100"));
    await user.click(screen.getByRole("button", { name: "新增品項" }));
    await waitFor(() => expect(posted).toContain("拿鐵"));
    expect(JSON.parse(posted).unit_cost).toBe("60");
    expect(JSON.parse(posted).unit_price).toBe("100");
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
    await user.click(screen.getByRole("button", { name: "新增品項" }));
    await user.type(screen.getByLabelText("成本（整數元，選填）"), "60");
    // 費率 0：60÷0.7=85.71 → ×1.05 = 90（少補手續費，不把客人的價格墊高）
    await waitFor(() =>
      expect((screen.getByLabelText("售價（整數元）") as HTMLInputElement).value).toBe("90"),
    );
    // 但既有品項的預估毛利率不可用 0 費率硬算（會高估店家收益）
    const row = screen.getByText("手沖-耶加").closest("tr")!;
    expect(row.cells[5].textContent).toBe("—");
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
    await user.click(screen.getByRole("button", { name: "新增品項" }));
    await user.type(screen.getByLabelText("品名"), "白開水");
    await user.type(screen.getByLabelText("售價（整數元）"), "10");
    await user.click(screen.getByRole("button", { name: "新增品項" }));
    await waitFor(() => expect(posted).toContain("白開水"));
    expect(JSON.parse(posted).unit_cost).toBe(null);
  });

  it("編輯改成本：PATCH unit_cost，存好刷新清單", async () => {
    let patched = "";
    let listCalls = 0;
    stubFetch((url, method, body) => {
      if (url.includes("/menu-items/1") && method === "PATCH") {
        patched = body;
        return json({ ...ITEMS[0], unit_cost: "70" });
      }
      if (url.includes("/menu-option-groups")) return json([]);
      if (url.includes("/menu-items")) {
        listCalls += 1;
        return json(ITEMS);
      }
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    await screen.findByText("手沖-耶加");
    await user.click(screen.getByRole("button", { name: "手沖-耶加 編輯" }));
    const dialog = await screen.findByRole("dialog", { name: "編輯 手沖-耶加" });
    const input = within(dialog).getByLabelText("成本");
    await user.clear(input);
    await user.type(input, "70");
    const before = listCalls;
    await user.click(within(dialog).getByRole("button", { name: "儲存" }));
    await waitFor(() => expect(patched).toContain("unit_cost"));
    expect(JSON.parse(patched)).toEqual({ unit_cost: "70" });
    await waitFor(() => expect(listCalls).toBeGreaterThan(before));
    expect(screen.queryByRole("dialog", { name: "編輯 手沖-耶加" })).toBeNull();
  });

  it("編輯清空成本＝把成本改回未知（送 null）", async () => {
    let patched = "";
    stubFetch((url, method, body) => {
      if (url.includes("/menu-items/1") && method === "PATCH") {
        patched = body;
        return json({ ...ITEMS[0], unit_cost: null });
      }
      if (url.includes("/menu-option-groups")) return json([]);
      if (url.includes("/menu-items")) return json(ITEMS);
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    await screen.findByText("手沖-耶加");
    await user.click(screen.getByRole("button", { name: "手沖-耶加 編輯" }));
    const dialog = await screen.findByRole("dialog", { name: "編輯 手沖-耶加" });
    await user.clear(within(dialog).getByLabelText("成本"));
    await user.click(within(dialog).getByRole("button", { name: "儲存" }));
    await waitFor(() => expect(patched).toContain("unit_cost"));
    expect(JSON.parse(patched).unit_cost).toBe(null);
  });

  it("刪除：確認後打 delete 端點；賣過的顯示後端給的原因", async () => {
    const calls: string[] = [];
    stubFetch((url, method) => {
      if (url.includes("/menu-items/1/delete") && method === "DELETE") {
        calls.push(url);
        return json({ detail: "這個品項賣過了，不能刪除，只能下架（交易紀錄要留著）" }, 409);
      }
      if (url.includes("/menu-items")) return json(ITEMS);
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    await screen.findByText("手沖-耶加");
    await user.click(screen.getAllByRole("button", { name: "刪除" })[0]);
    // 站內確認視窗（不是瀏覽器的 confirm）：按下去才真的送出
    const dialog = await screen.findByRole("dialog", { name: "刪除品項" });
    expect(calls).toEqual([]);
    await user.click(within(dialog).getByRole("button", { name: "刪除" }));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(await screen.findByText(/賣過了/)).toBeTruthy();
  });

  it("刪除：取消確認就不送出（誤按不該讓商品消失）", async () => {
    const calls: string[] = [];
    stubFetch((url, method) => {
      if (url.includes("/delete") && method === "DELETE") {
        calls.push(url);
        return json(null, 204);
      }
      if (url.includes("/menu-items")) return json(ITEMS);
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    await screen.findByText("手沖-耶加");
    await user.click(screen.getAllByRole("button", { name: "刪除" })[0]);
    const dialog = await screen.findByRole("dialog", { name: "刪除品項" });
    await user.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(calls).toEqual([]);
  });
});
