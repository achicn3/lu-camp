// @vitest-environment jsdom
// 「不知道喝什麼」引導推薦設定（docs/63 §2 M2a；店主 2026-10-09）：每個答案勾適合的品項或體驗卡。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { QuizForm } from "@/features/menu/QuizForm";
import { QuizSection } from "@/features/menu/QuizSection";

function response(body: unknown, status = 200) {
  return new Response(body === null ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const LATTE = {
  id: 3, store_id: 1, name: "拿鐵", unit_price: "150", unit_cost: null, category_id: 1, category: "咖啡",
  description: null, is_available: true, sort_order: 0, photo_url: null, daily_limited: false, option_groups: [],
};
const CAKE = { ...LATTE, id: 4, name: "戚風", category: "甜點" };
const BREW_CARD = {
  id: 9, menu_item_id: 3, option_ids: [], title: "蜜桃手沖體驗", tag: null, origin: null, notes: null,
  description: null, includes: [], theme: "peach", art: "peach", effect: "random", is_active: true, sort_order: 0,
};
const DEFAULT = {
  is_active: false,
  is_default: true,
  questions: [
    { prompt: "今天想來點什麼？", options: [{ label: "咖啡", items: [] }, { label: "想吃甜的", items: [] }] },
  ],
};

function withClient(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

function mountForm(initial = DEFAULT, onDone = vi.fn()) {
  withClient(
    <QuizForm
      initial={initial as never}
      items={[LATTE, CAKE] as never}
      experiences={[BREW_CARD] as never}
      onDone={onDone}
      onCancel={vi.fn()}
    />,
  );
  return onDone;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("摘要：還沒設定時說明會用預設題目，連到設定頁", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => response(DEFAULT)));
  withClient(<QuizSection />);
  expect(await screen.findByText(/還沒設定/)).toBeTruthy();
  expect(screen.getByRole("link", { name: "設定問答" }).getAttribute("href")).toBe("/menu/quiz");
});

it("摘要：啟用中顯示幾題、勾了幾個品項", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      response({
        ...DEFAULT,
        is_default: false,
        is_active: true,
        questions: [
          {
            prompt: "想喝什麼？",
            options: [
              { label: "咖啡", items: [{ kind: "item", id: 3 }, { kind: "experience", id: 9 }] },
              { label: "甜的", items: [{ kind: "item", id: 4 }] },
            ],
          },
        ],
      }),
    ),
  );
  withClient(<QuizSection />);
  expect(await screen.findByText("顯示中・1 題・勾了 3 個品項")).toBeTruthy();
});

it("每個答案勾品項或體驗卡、改題目文字、打開顯示，存檔送出整份", async () => {
  let body: unknown;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request) => {
      body = await input.json();
      return response({ ...(body as object), is_default: false });
    }),
  );
  const done = mountForm();
  const user = userEvent.setup();

  const prompt = screen.getByLabelText("題目 1");
  await user.clear(prompt);
  await user.type(prompt, "想喝點什麼？");
  await user.selectOptions(screen.getByLabelText("第 1 題答案 1 加入品項"), "item:3");
  await user.selectOptions(screen.getByLabelText("第 1 題答案 1 加入品項"), "experience:9");
  await user.selectOptions(screen.getByLabelText("第 1 題答案 2 加入品項"), "item:4");
  const first = screen.getByRole("group", { name: "第 1 題答案 1" });
  expect(within(first).getByText("拿鐵")).toBeTruthy();
  expect(within(first).getByText("手沖體驗：蜜桃手沖體驗")).toBeTruthy();
  await user.click(screen.getByLabelText("在客人頁顯示「不知道喝什麼？」"));
  await user.click(screen.getByRole("button", { name: "儲存" }));

  await waitFor(() => expect(done).toHaveBeenCalled());
  expect(body).toEqual({
    is_active: true,
    questions: [
      {
        prompt: "想喝點什麼？",
        options: [
          { label: "咖啡", items: [{ kind: "item", id: 3 }, { kind: "experience", id: 9 }] },
          { label: "想吃甜的", items: [{ kind: "item", id: 4 }] },
        ],
      },
    ],
  });
});

it("勾過的品項可以移除；同一個答案不會出現重複的選項", async () => {
  mountForm();
  const user = userEvent.setup();
  const picker = screen.getByLabelText("第 1 題答案 1 加入品項");
  await user.selectOptions(picker, "item:3");
  expect(within(picker).queryByRole("option", { name: "拿鐵" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "移除 拿鐵（第 1 題答案 1）" }));
  expect(within(picker).getByRole("option", { name: "拿鐵" })).toBeTruthy();
});

it("題數 1–3、答案 2–4；文字空白不能存", async () => {
  mountForm();
  const user = userEvent.setup();
  expect(screen.getByRole("button", { name: "刪除第 1 題" })).toHaveProperty("disabled", true);
  expect(screen.getByRole("button", { name: "移除第 1 題答案 1" })).toHaveProperty("disabled", true);
  await user.click(screen.getByRole("button", { name: "＋ 加一題" }));
  await user.click(screen.getByRole("button", { name: "＋ 加一題" }));
  expect(screen.getByRole("button", { name: "＋ 加一題" })).toHaveProperty("disabled", true);
  await user.click(screen.getByRole("button", { name: "＋ 第 1 題加一個答案" }));
  await user.click(screen.getByRole("button", { name: "＋ 第 1 題加一個答案" }));
  expect(screen.getByRole("button", { name: "＋ 第 1 題加一個答案" })).toHaveProperty("disabled", true);
  expect(screen.getByRole("button", { name: "儲存" })).toHaveProperty("disabled", true);
  expect(screen.getByText("每一題和每個答案都要有文字。")).toBeTruthy();
});

it("打開顯示但一個品項都沒勾：提醒客人頁不會出現", async () => {
  mountForm();
  const user = userEvent.setup();
  await user.click(screen.getByLabelText("在客人頁顯示「不知道喝什麼？」"));
  expect(screen.getByText(/還沒勾任何品項/)).toBeTruthy();
});
