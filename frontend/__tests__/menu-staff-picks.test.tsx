// @vitest-environment jsdom
// 店員推薦（店主 2026-10-10）：線上發布分頁一份清單，可挑餐飲品項、手沖體驗卡、帶著走商品並排順序。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { StaffPicksSection } from "@/features/menu/StaffPicksSection";

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const LATTE = {
  id: 3, store_id: 1, name: "拿鐵", unit_price: "150", unit_cost: null, category_id: 1, category: "咖啡",
  description: null, is_available: true, sort_order: 0, photo_url: null, daily_limited: false, option_groups: [],
};
const CAKE = { ...LATTE, id: 4, name: "戚風", category: "甜點" };
const CARD = {
  id: 9, menu_item_id: 3, option_ids: [], title: "蜜桃手沖體驗", tag: null, origin: null, notes: null,
  description: null, includes: [], theme: "peach", art: "peach", effect: "random", is_active: true, sort_order: 0,
};
const BEAN = {
  id: 2, catalog_product_id: 40, product_name: "耶加雪菲豆", product_active: true, unit_price: "450",
  quantity_on_hand: 3, category_name: "咖啡豆", description: null, role: "bean", is_active: true, sort_order: 0,
};

function routes(picks: unknown, onPut?: (body: unknown) => void) {
  return vi.fn(async (input: Request) => {
    const path = new URL(input.url).pathname;
    if (path.endsWith("/staff-picks") && input.method === "PUT") {
      const body = await input.json();
      onPut?.(body);
      return response(body);
    }
    if (path.endsWith("/staff-picks")) return response(picks);
    if (path.endsWith("/menu-items")) return response([LATTE, CAKE]);
    if (path.endsWith("/experiences")) return response([CARD]);
    if (path.endsWith("/online-order/retail")) return response([BEAN]);
    return response({ detail: "unexpected" }, 404);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <StaffPicksSection />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const names = () =>
  within(screen.getByRole("list", { name: "店員推薦清單" }))
    .getAllByRole("listitem")
    .map((li) => li.querySelector(".staff-pick-name")?.textContent);

it("列出目前的推薦（三種東西都看得懂名字），可加、可調順序、可移除，存檔送出新順序", async () => {
  let saved: unknown;
  vi.stubGlobal("fetch", routes({ items: [{ kind: "item", id: 4 }] }, (body) => { saved = body; }));
  mount();
  const user = userEvent.setup();
  await screen.findByText("戚風");

  await user.selectOptions(screen.getByLabelText("加入店員推薦"), "experience:9");
  await user.selectOptions(screen.getByLabelText("加入店員推薦"), "retail:40");
  expect(names()).toEqual(["戚風", "手沖體驗：蜜桃手沖體驗", "帶著走：耶加雪菲豆"]);

  await user.click(screen.getByRole("button", { name: "上移 帶著走：耶加雪菲豆" }));
  await user.click(screen.getByRole("button", { name: "移除 戚風" }));
  expect(names()).toEqual(["帶著走：耶加雪菲豆", "手沖體驗：蜜桃手沖體驗"]);
  // 已加入的不會再出現在下拉選單
  expect(within(screen.getByLabelText("加入店員推薦")).queryByRole("option", { name: "帶著走：耶加雪菲豆" })).toBeNull();

  await user.click(screen.getByRole("button", { name: "儲存店員推薦" }));
  await waitFor(() =>
    expect(saved).toEqual({ items: [{ kind: "retail", id: 40 }, { kind: "experience", id: 9 }] }),
  );
  expect(await screen.findByText(/已儲存/)).toBeTruthy();
});

it("第一項不能上移、最後一項不能下移", async () => {
  vi.stubGlobal("fetch", routes({ items: [{ kind: "item", id: 3 }, { kind: "item", id: 4 }] }));
  mount();
  expect((await screen.findByRole("button", { name: "上移 拿鐵" })).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "下移 戚風" }).hasAttribute("disabled")).toBe(true);
});

it("還沒有推薦：說明客人頁不會有這個分頁", async () => {
  vi.stubGlobal("fetch", routes({ items: [] }));
  mount();
  expect(await screen.findByText(/還沒有店員推薦/)).toBeTruthy();
});
