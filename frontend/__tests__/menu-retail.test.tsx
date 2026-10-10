// @vitest-environment jsdom
// 線上「帶回家」零售商品管理（docs/63 §13、M1d）：從現有商品挑上線，價格與庫存跟著原商品。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { RetailForm } from "@/features/menu/RetailForm";
import { RetailSection } from "@/features/menu/RetailSection";

function response(body: unknown, status = 200) {
  return new Response(body === null ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
const LISTING = {
  id: 7, catalog_product_id: 41, description: "柑橘、茉莉", role: "bean", is_active: true, sort_order: 0,
  photo_sha256: null, product_name: "耶加雪菲 200g", unit_price: "450", quantity_on_hand: 3,
  product_active: true, category_name: "咖啡豆",
};
const PRODUCT = {
  id: 41, store_id: 1, sku: "BEAN-200", name: "耶加雪菲 200g", brand_id: null, product_model_id: null,
  category_id: 2, unit_price: "450", unit_cost: "220", quantity_on_hand: 3, reorder_point: 0,
  is_active: true, note: null, incoming_qty: 0,
};

function wrap(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("列出帶回家商品：名稱、售價、庫存、分類，編輯連到獨立頁", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => response([LISTING])));
  wrap(<RetailSection />);
  const row = (await screen.findByText("耶加雪菲 200g")).closest("li")!;
  expect(row.textContent).toContain("$450");
  expect(row.textContent).toContain("庫存 3");
  expect(row.textContent).toContain("咖啡豆");
  expect(row.textContent).toContain("加購：咖啡豆");
  expect(within(row).getByRole("link", { name: "編輯" }).getAttribute("href")).toBe("/menu/retail/7");
  expect(screen.getByRole("link", { name: "新增帶著走商品" }).getAttribute("href")).toBe("/menu/retail/new");
});

it("新增：搜尋現有商品挑一個，送出的內容不含價格與庫存", async () => {
  let body: Record<string, unknown> | undefined;
  vi.stubGlobal("fetch", vi.fn(async (input: Request) => {
    if (input.url.includes("/catalog-products")) {
      expect(new URL(input.url).searchParams.get("q")).toBe("耶加");
      return response([PRODUCT]);
    }
    if (input.method === "POST") { body = await input.json(); return response({ ...LISTING, id: 8 }, 201); }
    return response([]);
  }));
  const done = vi.fn();
  wrap(<RetailForm initial={null} onDone={done} onCancel={vi.fn()} />);
  const user = userEvent.setup();
  const form = screen.getByRole("form", { name: "帶著走商品" });
  await user.type(within(form).getByLabelText("搜尋商品"), "耶加");
  await user.click(await within(form).findByRole("radio", { name: /耶加雪菲 200g/ }));
  await user.type(within(form).getByLabelText("介紹"), "柑橘、茉莉");
  await user.selectOptions(within(form).getByLabelText("加購角色"), "bean");
  await user.click(within(form).getByRole("button", { name: "儲存" }));
  await waitFor(() => expect(done).toHaveBeenCalled());
  expect(body).toEqual({
    catalog_product_id: 41, description: "柑橘、茉莉", role: "bean", is_active: true, sort_order: 0,
  });
});

it("沒挑商品不能存", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => response([])));
  wrap(<RetailForm initial={null} onDone={vi.fn()} onCancel={vi.fn()} />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "儲存" }));
  expect((await screen.findByRole("alert")).textContent).toContain("請先挑一個商品");
});

it("編輯：商品固定、可以上傳照片（走帶回家商品的照片端點）", async () => {
  const urls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: Request) => {
    urls.push(`${input.method} ${new URL(input.url).pathname}`);
    return response({ ...LISTING, photo_sha256: "a".repeat(64) });
  }));
  wrap(<RetailForm initial={LISTING as never} onDone={vi.fn()} onCancel={vi.fn()} />);
  const form = screen.getByRole("form", { name: "帶著走商品" });
  expect(within(form).queryByLabelText("搜尋商品")).toBeNull();
  expect(form.textContent).toContain("耶加雪菲 200g");
  const user = userEvent.setup();
  const file = new File(["x"], "bean.jpg", { type: "image/jpeg" });
  await user.upload(within(form).getByLabelText("耶加雪菲 200g 上傳照片"), file);
  await waitFor(() => expect(urls).toContain("POST /api/v1/online-order/retail/7/photo"));
});
