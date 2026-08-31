// @vitest-environment jsdom
// POS 結帳的會員查找要像收購頁一樣「打字就出結果」，不必先按查詢。
//
// 為什麼值得改：結帳當下客人站在櫃檯前，多一個按鈕就多一次停頓。收購頁已經是即時的，
// 兩邊行為不一致本身也是負擔——店員得記住「這頁要按、那頁不用」。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { MemberPanel } from "@/features/pos/MemberPanel";

const MEMBER = { id: 7, store_id: 1, name: "林測試", phone: "0912345678", roles: ["MEMBER"] };

let contactCalls = 0;

function stubFetch(results: unknown[] = [MEMBER]) {
  contactCalls = 0;
  vi.stubGlobal(
    "fetch",
    // openapi-fetch 是以 Request 物件呼叫 fetch 的，String() 只會得到
    // "[object Request]"——比對網址前得先把它取出來，否則所有分支都不中，
    // 測試會以為元件壞了（實際上壞的是 stub）。
    vi.fn(async (input: unknown) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.includes("/contacts?") || url.includes("/contacts&")) {
        contactCalls += 1;
        return new Response(JSON.stringify(results), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/store-credit")) {
        return new Response(JSON.stringify({ balance: "500" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response("null", { status: 200, headers: { "Content-Type": "application/json" } });
    }),
  );
}

function renderPanel(node: ReactNode) {
  render(<QueryClientProvider client={new QueryClient()}>{node}</QueryClientProvider>);
}

beforeEach(() => stubFetch());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("POS 會員即時查找", () => {
  it("打字就出結果，不必按任何按鈕", async () => {
    renderPanel(<MemberPanel member={null} onSelect={vi.fn()} onClear={vi.fn()} />);

    await userEvent.type(screen.getByPlaceholderText("姓名或電話"), "0912");

    expect(await screen.findByRole("button", { name: /林測試/ })).toBeDefined();
  });

  it("點選結果會把會員交給呼叫端", async () => {
    const onSelect = vi.fn();
    renderPanel(<MemberPanel member={null} onSelect={onSelect} onClear={vi.fn()} />);

    await userEvent.type(screen.getByPlaceholderText("姓名或電話"), "0912");
    await userEvent.click(await screen.findByRole("button", { name: /林測試/ }));

    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }));
  });

  it("查無會員時說清楚，而不是靜靜地沒反應", async () => {
    stubFetch([]);
    renderPanel(<MemberPanel member={null} onSelect={vi.fn()} onClear={vi.fn()} />);

    await userEvent.type(screen.getByPlaceholderText("姓名或電話"), "0000");

    expect(await screen.findByText(/查無/)).toBeDefined();
  });

  it("清空輸入的當下就不再顯示舊結果，不等 debounce 過去", async () => {
    // 送查的字串刻意落後輸入框 250ms。若只看查詢結果、不看輸入框現在有沒有字，
    // 清空後那 250ms 內畫面仍掛著上一次的清單——店員會以為那是新輸入的結果。
    renderPanel(<MemberPanel member={null} onSelect={vi.fn()} onClear={vi.fn()} />);
    const box = screen.getByPlaceholderText("姓名或電話");

    await userEvent.type(box, "0912");
    await screen.findByRole("button", { name: /林測試/ });
    await userEvent.clear(box);

    // 不用 waitFor：等下去 debounce 就過了，那樣無論實作怎麼寫都會過。
    expect(screen.queryByRole("button", { name: /林測試/ })).toBeNull();
  });

  it("清空輸入 → 不查、也不留著上一次的結果", async () => {
    renderPanel(<MemberPanel member={null} onSelect={vi.fn()} onClear={vi.fn()} />);
    const box = screen.getByPlaceholderText("姓名或電話");

    await userEvent.type(box, "0912");
    await screen.findByRole("button", { name: /林測試/ });
    await userEvent.clear(box);

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /林測試/ })).toBeNull(),
    );
  });

  it("連續打字只查一次——每個按鍵都打一次 API 會讓後端白忙", async () => {
    renderPanel(<MemberPanel member={null} onSelect={vi.fn()} onClear={vi.fn()} />);

    await userEvent.type(screen.getByPlaceholderText("姓名或電話"), "0912345678");
    await screen.findByRole("button", { name: /林測試/ });

    expect(contactCalls).toBeLessThanOrEqual(2);
  });

  it("停用時不可查詢（結帳中/購物車凍結）", async () => {
    renderPanel(<MemberPanel member={null} onSelect={vi.fn()} onClear={vi.fn()} disabled />);

    expect(screen.getByPlaceholderText("姓名或電話")).toHaveProperty("disabled", true);
  });
});
