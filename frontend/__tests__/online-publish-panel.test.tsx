// @vitest-environment jsdom
// 菜單頁「線上點餐」區塊（docs/44 §3.5、§4.1；O3b）：發佈菜單、上次發佈時間、各桌網址、重發桌位碼。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OnlinePublishPanel } from "@/features/menu/OnlinePublishPanel";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Call = { url: string; method: string };

function stubFetch(route: (url: string, method: string) => Response): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      calls.push({ url, method });
      return route(url, method);
    }),
  );
  return calls;
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

const STATUS = {
  configured: true,
  last_version: 1790900000000,
  last_published_at: "2026-10-02T03:00:00Z",
  tables: [
    { label: "A1", service_mode: "DINE_IN", code: "c1".repeat(11), url: "https://order.test/t/c1" },
    { label: "外帶", service_mode: "TAKEOUT", code: "c2".repeat(11), url: "https://order.test/t/c2" },
  ],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("線上點餐區塊", () => {
  it("顯示上次發佈時間與各桌網址", async () => {
    stubFetch(() => json(STATUS));
    wrap(<OnlinePublishPanel />);
    const region = await screen.findByRole("region", { name: "線上點餐" });
    expect(await within(region).findByText(/上次發佈/)).toBeTruthy();
    expect(within(region).getByText("https://order.test/t/c1")).toBeTruthy();
    expect(within(region).getByText("外帶")).toBeTruthy();
  });

  it("按發佈：送出後顯示結果並重抓狀態", async () => {
    const calls = stubFetch((url, method) =>
      url.endsWith("/online-order/publish") && method === "POST"
        ? json({
            version: 1790900001000,
            published_at: "2026-10-02T04:00:00Z",
            item_count: 12,
            photos_pushed: 3,
            font_pushed: true,
          })
        : json(STATUS),
    );
    const user = userEvent.setup();
    wrap(<OnlinePublishPanel />);
    await user.click(await screen.findByRole("button", { name: "發佈到線上點餐" }));
    expect(await screen.findByText(/已發佈 12 道/)).toBeTruthy();
    await waitFor(() =>
      expect(calls.filter((c) => c.url.endsWith("/online-order/status")).length).toBeGreaterThan(1),
    );
  });

  it("發佈失敗顯示後端給的原因", async () => {
    stubFetch((url, method) =>
      method === "POST"
        ? json({ detail: "連不上線上點餐雲端，請確認網路後再按一次發佈" }, 502)
        : json(STATUS),
    );
    const user = userEvent.setup();
    wrap(<OnlinePublishPanel />);
    await user.click(await screen.findByRole("button", { name: "發佈到線上點餐" }));
    expect((await screen.findByRole("alert")).textContent).toContain("連不上線上點餐雲端");
  });

  it("重發桌位碼要先確認，確認後才送出", async () => {
    const calls = stubFetch((url, method) =>
      url.includes("/rotate") && method === "POST" ? json(STATUS.tables[0]) : json(STATUS),
    );
    const user = userEvent.setup();
    wrap(<OnlinePublishPanel />);
    await user.click(await screen.findByRole("button", { name: "A1 重發 QR" }));
    expect(calls.some((c) => c.url.includes("/rotate"))).toBe(false);
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("舊的 QR");
    await user.click(within(dialog).getByRole("button", { name: "重發" }));
    await waitFor(() =>
      expect(calls.some((c) => c.url.endsWith("/online-order/tables/A1/rotate"))).toBe(true),
    );
  });

  it("尚未設定雲端：說明、不能按發佈", async () => {
    stubFetch(() => json({ ...STATUS, configured: false, last_version: null, last_published_at: null, tables: [] }));
    wrap(<OnlinePublishPanel />);
    expect(await screen.findByText(/尚未設定/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "發佈到線上點餐" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });
});
