// @vitest-environment jsdom
// 「今天」區塊（docs/99 §8）：首頁與開店前檢查共用。過期的重複待辦合成一行、可一鍵略過；
// 讀不到要講出來，不能顯示成「今天沒事」。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push }),
  usePathname: () => "/",
}));

import type { AgendaItem, AgendaToday } from "@/features/agenda/calendar";
import { TodayPanel } from "@/features/agenda/TodayPanel";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function item(partial: Partial<AgendaItem> & Pick<AgendaItem, "key" | "day" | "title">): AgendaItem {
  return {
    kind: "TASK",
    end_day: null,
    start_time: null,
    end_time: null,
    note: null,
    status: "OPEN",
    overdue: false,
    manager_only: false,
    assignee_staff_id: null,
    assignee_name: null,
    lead_days: 3,
    task_id: null,
    series_id: null,
    occurrence_on: null,
    event_id: null,
    event_kind: null,
    campaign_id: null,
    campaign_status: null,
    href: null,
    ...partial,
  };
}

function today(partial: Partial<AgendaToday> = {}): AgendaToday {
  return {
    today: "2026-11-10",
    due_today: [],
    overdue: [],
    upcoming: [],
    events: [],
    campaigns: [],
    consignment_pending_count: 0,
    badge_count: 0,
    ...partial,
  };
}

type Call = { url: string; method: string };
let calls: Call[] = [];

function stubFetch(route: (url: string, method: string) => Response | null) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      calls.push({ url, method });
      const resp = route(url, method);
      if (resp) return resp;
      throw new Error(`unmatched fetch: ${method} ${url}`);
    }),
  );
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<TodayPanel />, { wrapper: Wrapper });
}

afterEach(() => {
  cleanup();
  push.mockReset();
  vi.unstubAllGlobals();
});

describe("今天區塊", () => {
  it("同一個重複待辦過期好幾次合成一行，按「都不用做了」略過全部", async () => {
    stubFetch((url, method) => {
      if (url.includes("/skip-overdue") && method === "POST") return json({ skipped: 2 });
      if (url.includes("/agenda/today"))
        return json(
          today({
            overdue: [
              item({ key: "occurrence:3:2026-11-02", day: "2026-11-02", title: "叫牛奶", series_id: 3, overdue: true }),
              item({ key: "occurrence:3:2026-11-05", day: "2026-11-05", title: "叫牛奶", series_id: 3, overdue: true }),
              item({ key: "task:9", day: "2026-11-08", title: "回覆廠商", task_id: 9, overdue: true }),
            ],
            badge_count: 3,
          }),
        );
      return null;
    });
    renderPanel();
    const user = userEvent.setup();

    expect(await screen.findByText("叫牛奶：2 次沒打勾")).toBeTruthy();
    expect(screen.getByText("11/2（一）、11/5（四）")).toBeTruthy();
    // 單次的照常一列、可以直接打勾
    expect(screen.getByRole("checkbox", { name: "回覆廠商" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "都不用做了" }));
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/agenda/task-series/3/skip-overdue"))).toBe(true),
    );
  });

  it("今天要做、快到期、寄售待撥款；點名稱到行事曆那一天", async () => {
    stubFetch((url) =>
      url.includes("/agenda/today")
        ? json(
            today({
              due_today: [item({ key: "task:1", day: "2026-11-10", title: "補發票紙", task_id: 1 })],
              upcoming: [item({ key: "task:2", day: "2026-11-12", title: "付房租", task_id: 2 })],
              consignment_pending_count: 2,
            }),
          )
        : null,
    );
    renderPanel();
    const user = userEvent.setup();

    const panel = await screen.findByRole("region", { name: "今天" });
    expect(await within(panel).findByText("今天要做")).toBeTruthy();
    expect(within(panel).getByText("11/12（四）")).toBeTruthy();
    expect(within(panel).getByRole("link", { name: "去寄售付款" }).getAttribute("href")).toBe(
      "/consignment",
    );

    await user.click(within(panel).getByRole("button", { name: "付房租" }));
    expect(push).toHaveBeenCalledWith("/calendar?day=2026-11-12");
  });

  it("沒有事要講清楚；讀不到要講出來，不能說成沒事", async () => {
    stubFetch((url) => (url.includes("/agenda/today") ? json(today()) : null));
    renderPanel();
    expect(await screen.findByText("今天沒有待辦，也沒有快到期的事。")).toBeTruthy();
    cleanup();

    stubFetch((url) => (url.includes("/agenda/today") ? json({ detail: "壞掉了" }, 500) : null));
    renderPanel();
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText("今天沒有待辦，也沒有快到期的事。")).toBeNull();
  });
});
