// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MenuPresentationDialog } from "@/features/menu/MenuPresentationDialog";

const defaults = { menu_item_id: 7, flavor_description: null, audience_description: null, is_recommended: false, is_new: false, limited_on: null, show_remaining: true, low_stock_threshold: 5, hide_sold_out: false };
function response(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
function mount(onDone = vi.fn(), onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MenuPresentationDialog itemId={7} itemName="拿鐵" onDone={onDone} onClose={onClose} /></QueryClientProvider>);
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("載入設定後可儲存風味、推薦與低庫存政策，不送價格或庫存", async () => {
  let body: Record<string, unknown> | undefined;
  vi.stubGlobal("fetch", vi.fn(async (input: Request) => {
    if (input.method === "PUT") { body = await input.json(); return response({ ...defaults, ...body }); }
    return response(defaults);
  }));
  const done = vi.fn(); mount(done);
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("風味描述"), "蜜桃・花香");
  await user.type(screen.getByLabelText("適合族群"), "喜歡果香的人");
  await user.click(screen.getByLabelText("露坑推薦"));
  await user.clear(screen.getByLabelText("低庫存顯示門檻"));
  await user.type(screen.getByLabelText("低庫存顯示門檻"), "3");
  await user.click(screen.getByLabelText("售完後完全隱藏"));
  await user.click(screen.getByRole("button", { name: "儲存設定" }));
  await waitFor(() => expect(done).toHaveBeenCalledTimes(1));
  expect(body).toEqual({ flavor_description: "蜜桃・花香", audience_description: "喜歡果香的人", is_recommended: true, is_new: false, limited_on: null, show_remaining: true, low_stock_threshold: 3, hide_sold_out: true });
});

it("讀取失敗不能把預設值蓋回後台，可以重試", async () => {
  let failed = true;
  vi.stubGlobal("fetch", vi.fn(async () => failed ? response({ detail: "無法取得設定" }, 503) : response(defaults)));
  mount(); await screen.findByText("無法取得設定");
  expect(screen.queryByRole("button", { name: "儲存設定" })).toBeNull();
  failed = false; await userEvent.click(screen.getByRole("button", { name: "重新讀取" }));
  await screen.findByLabelText("風味描述");
});

it("儲存失敗保留輸入並顯示錯誤，關閉不送出", async () => {
  const close = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (request: Request) => request.method === "PUT" ? response({ detail: "設定未儲存" }, 409) : response(defaults)));
  mount(vi.fn(), close);
  await userEvent.type(await screen.findByLabelText("風味描述"), "花香");
  await userEvent.click(screen.getByRole("button", { name: "儲存設定" }));
  await screen.findByText("設定未儲存");
  expect((screen.getByLabelText("風味描述") as HTMLInputElement).value).toBe("花香");
  await userEvent.click(screen.getByRole("button", { name: "關閉" }));
  expect(close).toHaveBeenCalledTimes(1);
});
