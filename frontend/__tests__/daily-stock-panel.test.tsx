// @vitest-environment jsdom
// 今日餐點數量面板（docs/44 §3.7）：設定附上畫面上的數字、加減走原子調整、
// 被結帳搶先改過時講清楚並重讀。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DailyStockPanel, type DailyStockEntry } from "@/features/menu/DailyStockPanel";
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

const CAKE: DailyStockEntry = {
  kind: "item",
  id: 3,
  label: "戚風",
  remaining: 0,
  set_today: false,
};

type Call = { url: string; body: string };

function stubFetch(respond: (url: string) => Response) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const body =
        input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
      calls.push({ url, body });
      return respond(url);
    }),
  );
  return calls;
}

function renderPanel(entries: DailyStockEntry[]) {
  setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<DailyStockPanel entries={entries} />, { wrapper: Wrapper });
}

afterEach(() => {
  cleanup();
  clearToken();
  vi.unstubAllGlobals();
});

describe("今日餐點數量面板", () => {
  it("今天還沒填：輸入份數按設定，送出時附上畫面上看到的 0", async () => {
    const calls = stubFetch(() => json({ ...CAKE, remaining: 8, set_today: true }));
    const user = userEvent.setup();
    renderPanel([CAKE]);
    expect(screen.getByText("今天還沒填")).toBeTruthy();
    await user.type(screen.getByLabelText("戚風 今日份數"), "8");
    await user.click(screen.getByRole("button", { name: "設定" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/set"))).toBe(true));
    const sent = calls.find((c) => c.url.includes("/menu-daily-stock/item/3/set"));
    expect(JSON.parse(sent!.body)).toEqual({ qty: 8, expected_remaining: 0 });
  });

  it("+1／−1 走加減（原子），不是覆寫", async () => {
    const calls = stubFetch(() => json({ ...CAKE, remaining: 5, set_today: true }));
    const user = userEvent.setup();
    renderPanel([{ ...CAKE, remaining: 4, set_today: true }]);
    await user.click(screen.getByRole("button", { name: "戚風 加一份" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/adjust"))).toBe(true));
    const sent = calls.find((c) => c.url.includes("/menu-daily-stock/item/3/adjust"));
    expect(JSON.parse(sent!.body)).toEqual({ delta: 1, reason: "RESTOCK" });
  });

  it("−1 先選原因（報廢）才送出", async () => {
    const calls = stubFetch(() => json({ ...CAKE, remaining: 3, set_today: true }));
    const user = userEvent.setup();
    renderPanel([{ ...CAKE, remaining: 4, set_today: true }]);
    await user.click(screen.getByRole("button", { name: "戚風 減一份" }));
    expect(calls.some((c) => c.url.includes("/adjust"))).toBe(false);
    await user.click(screen.getByRole("button", { name: "報廢" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/adjust"))).toBe(true));
    const sent = calls.find((c) => c.url.includes("/adjust"));
    expect(JSON.parse(sent!.body)).toEqual({ delta: -1, reason: "WASTE" });
  });

  it("已售完時不能再 −1", () => {
    stubFetch(() => json([]));
    renderPanel([{ ...CAKE, remaining: 0, set_today: true }]);
    expect(screen.getByText("今天已售完")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "戚風 減一份" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("被結帳搶先改過：顯示後端訊息，並重讀清單", async () => {
    const calls = stubFetch((url) =>
      url.includes("/set")
        ? json({ detail: "「戚風」的數量剛剛變動（現在剩 2 份），請重新確認" }, 409)
        : json([{ ...CAKE, remaining: 2, set_today: true }]),
    );
    const user = userEvent.setup();
    renderPanel([{ ...CAKE, remaining: 3, set_today: true }]);
    const input = screen.getByLabelText("戚風 今日份數");
    await user.clear(input);
    await user.type(input, "10");
    await user.click(screen.getByRole("button", { name: "改成" }));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "「戚風」的數量剛剛變動（現在剩 2 份），請重新確認",
    );
    const sent = calls.find((c) => c.url.includes("/set"));
    expect(JSON.parse(sent!.body)).toEqual({ qty: 10, expected_remaining: 3 });
  });

  it("份數不是 0–9999 的整數時不能送出", async () => {
    stubFetch(() => json([]));
    const user = userEvent.setup();
    renderPanel([CAKE]);
    await user.type(screen.getByLabelText("戚風 今日份數"), "-3");
    expect((screen.getByRole("button", { name: "設定" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });
});
