// @vitest-environment jsdom
// 人氣標籤設定（docs/63 §7 M2b；店主 2026-10-10）：只用 POS 真實成交；後台開關、天數、門檻，看得到目前的榜。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { PopularitySection } from "@/features/menu/PopularitySection";

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const CURRENT = {
  is_active: true,
  window_days: 30,
  min_qty: 10,
  ranking: [
    { category: "咖啡", item_id: 3, name: "拿鐵", rank: 1, qty: 42 },
    { category: "咖啡", item_id: 4, name: "美式", rank: 2, qty: 18 },
    { category: "甜點", item_id: 7, name: "戚風", rank: 1, qty: 11 },
  ],
};

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PopularitySection />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("顯示目前設定與各分類的榜（名次、份數）", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => response(CURRENT)));
  mount();
  const board = await screen.findByRole("list", { name: "目前的人氣榜" });
  const rows = within(board).getAllByRole("listitem").map((li) => li.textContent);
  expect(rows).toEqual(["咖啡：拿鐵 人氣 No.1（42 份）", "咖啡：美式 人氣推薦（18 份）", "甜點：戚風 人氣 No.1（11 份）"]);
  expect((screen.getByLabelText("計算期間") as HTMLSelectElement).value).toBe("30");
  expect((screen.getByLabelText("至少賣出幾份才上榜") as HTMLInputElement).value).toBe("10");
});

it("改天數、門檻、關掉後儲存，送出設定", async () => {
  let body: unknown;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      if (input.method === "PUT") {
        body = await input.json();
        return response({ ...(body as object), ranking: [] });
      }
      return response(CURRENT);
    }),
  );
  mount();
  const user = userEvent.setup();
  await screen.findByRole("list", { name: "目前的人氣榜" });
  await user.selectOptions(screen.getByLabelText("計算期間"), "7");
  const min = screen.getByLabelText("至少賣出幾份才上榜");
  await user.clear(min);
  await user.type(min, "5");
  await user.click(screen.getByLabelText("在線上菜單顯示人氣標籤"));
  await user.click(screen.getByRole("button", { name: "儲存人氣設定" }));

  await waitFor(() => expect(body).toEqual({ is_active: false, window_days: 7, min_qty: 5 }));
  expect(await screen.findByText("已儲存，幾秒內客人頁就會更新。")).toBeTruthy();
});

it("門檻要 1–999 的整數才能存", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => response(CURRENT)));
  mount();
  const user = userEvent.setup();
  const min = await screen.findByLabelText("至少賣出幾份才上榜");
  await user.clear(min);
  await user.type(min, "0");
  expect(screen.getByRole("button", { name: "儲存人氣設定" })).toHaveProperty("disabled", true);
});

it("還沒有品項達到門檻：說明不會顯示標籤", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => response({ ...CURRENT, ranking: [] })));
  mount();
  expect(await screen.findByText(/還沒有品項達到門檻/)).toBeTruthy();
});
