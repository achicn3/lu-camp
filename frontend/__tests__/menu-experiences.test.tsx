// @vitest-environment jsdom
// 手沖體驗卡管理（docs/63 §4、M1c）：引用既有品項＋預選選項，不填價格。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { ExperienceForm } from "@/features/menu/ExperienceForm";
import { ExperienceSection } from "@/features/menu/ExperienceSection";

function response(body: unknown, status = 200) {
  return new Response(body === null ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
const BREW = {
  id: 3, store_id: 1, name: "手沖咖啡", unit_price: "220", unit_cost: null, category_id: 1, category: "咖啡",
  description: null, is_available: true, sort_order: 0, photo_url: null, daily_limited: false,
  option_groups: [
    { id: 10, name: "豆子", min_select: 1, max_select: 1, sort_order: 0, options: [
      { id: 11, name: "蜜桃蹦蹦", price_delta: "60", is_available: true, sort_order: 0 },
      { id: 12, name: "天堂鳥莊園", price_delta: "20", is_available: true, sort_order: 1 },
    ] },
    { id: 20, name: "溫度", min_select: 1, max_select: 1, sort_order: 1, options: [
      { id: 21, name: "熱", price_delta: "0", is_available: true, sort_order: 0 },
    ] },
  ],
};
const CAKE = { ...BREW, id: 4, name: "戚風", option_groups: [] };
const SAVED = {
  id: 9, menu_item_id: 3, option_ids: [11], title: "蜜桃蹦蹦手沖體驗", tag: "清甜果香", origin: null, notes: null,
  description: null, includes: [{ title: "咖啡豆", detail: "現磨" }], theme: "peach", art: "peach", effect: "random",
  is_active: true, sort_order: 0,
};

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ExperienceSection /></QueryClientProvider>);
}
function mountForm(onDone = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>
    <ExperienceForm initial={null} items={[BREW, CAKE] as never} onDone={onDone} onCancel={vi.fn()} />
  </QueryClientProvider>);
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("列出體驗卡：標題、原品項與預選的豆子", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: Request) => {
    if (input.url.includes("/menu-items")) return response([BREW, CAKE]);
    return response([SAVED]);
  }));
  mount();
  const row = (await screen.findByText("蜜桃蹦蹦手沖體驗")).closest("li")!;
  expect(within(row).getByText(/手沖咖啡/)).toBeTruthy();
  expect(within(row).getByText(/蜜桃蹦蹦$/)).toBeTruthy();
  expect(within(row).getByRole("link", { name: "編輯" }).getAttribute("href")).toBe("/menu/experiences/9");
  expect(screen.getByRole("link", { name: "新增體驗卡" }).getAttribute("href")).toBe("/menu/experiences/new");
});

it("新增：選原品項後出現它的選項可預選；送出的內容不含價格", async () => {
  let body: Record<string, unknown> | undefined;
  vi.stubGlobal("fetch", vi.fn(async (input: Request) => {
    if (input.url.includes("/menu-items")) return response([BREW, CAKE]);
    if (input.method === "POST") { body = await input.json(); return response({ ...SAVED, ...body, id: 10 }, 201); }
    return response([]);
  }));
  const done = vi.fn();
  mountForm(done);
  const user = userEvent.setup();
  const form = screen.getByRole("form", { name: "體驗卡" });
  await user.selectOptions(within(form).getByLabelText("原品項"), "3");
  await user.click(within(form).getByLabelText("蜜桃蹦蹦（+$60）"));
  // 卡面預覽跟著輸入即時更新，價格＝原品項＋預選（還要選溫度 → 起）
  expect(within(screen.getByRole("article", { name: "卡面預覽" })).getByText("$280 起")).toBeTruthy();
  await user.type(within(form).getByLabelText("卡片標題"), "蜜桃蹦蹦手沖體驗");
  await user.type(within(form).getByLabelText("標籤"), "清甜果香");
  // 新卡先帶入店主認可的三項包含內容，可以改
  expect((within(form).getByLabelText("包含項目 3") as HTMLInputElement).value).toBe("現場體驗");
  await user.clear(within(form).getByLabelText("項目 1 說明"));
  await user.type(within(form).getByLabelText("項目 1 說明"), "現磨");
  await user.selectOptions(within(form).getByLabelText("抽卡動畫"), "truck");
  await user.click(within(form).getByLabelText("蜜桃粉"));
  await user.click(within(form).getByRole("button", { name: "儲存體驗卡" }));
  await waitFor(() => expect(body).toBeDefined());
  expect(body).toMatchObject({
    menu_item_id: 3, option_ids: [11], title: "蜜桃蹦蹦手沖體驗", tag: "清甜果香",
    theme: "peach", effect: "truck", is_active: true,
  });
  expect((body?.includes as unknown[])[0]).toEqual({ title: "咖啡豆", detail: "現磨" });
  expect(body?.includes).toHaveLength(3);
  expect(JSON.stringify(body)).not.toMatch(/price/);
  await waitFor(() => expect(done).toHaveBeenCalledTimes(1));
});

it("伺服器擋下（預選不合法）時顯示原因、保留輸入", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: Request) => {
    if (input.url.includes("/menu-items")) return response([BREW]);
    if (input.method === "POST") return response({ detail: "「豆子」最多只能預選 1 項" }, 422);
    return response([]);
  }));
  mountForm();
  const user = userEvent.setup();
  const form = screen.getByRole("form", { name: "體驗卡" });
  await user.selectOptions(within(form).getByLabelText("原品項"), "3");
  await user.type(within(form).getByLabelText("卡片標題"), "測試");
  await user.click(within(form).getByRole("button", { name: "儲存體驗卡" }));
  await screen.findByText("「豆子」最多只能預選 1 項");
  expect((within(form).getByLabelText("卡片標題") as HTMLInputElement).value).toBe("測試");
});

it("刪除要在畫面上再確認一次", async () => {
  const deleted = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (input: Request) => {
    if (input.url.includes("/menu-items")) return response([BREW]);
    if (input.method === "DELETE") { deleted(); return response(null, 204); }
    return response([SAVED]);
  }));
  mount();
  const user = userEvent.setup();
  const row = (await screen.findByText("蜜桃蹦蹦手沖體驗")).closest("li")!;
  await user.click(within(row).getByRole("button", { name: "刪除" }));
  expect(deleted).not.toHaveBeenCalled();
  await user.click(within(row).getByRole("button", { name: "確定刪除" }));
  await waitFor(() => expect(deleted).toHaveBeenCalledTimes(1));
});
