// @vitest-environment jsdom
// /calendar 行事曆（docs/99 §9）：月曆格與當天清單、打勾走對的 API、新增單次與重複待辦、篩選。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/calendar",
  useSearchParams: () => new URLSearchParams("day=2026-11-10"),
}));

import CalendarPage from "@/app/(authed)/calendar/page";
import type { AgendaItem } from "@/features/agenda/calendar";
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

const ITEMS: AgendaItem[] = [
  item({ key: "event:4:2026-11-09", kind: "EVENT", day: "2026-11-09", title: "公休", event_id: 4, event_kind: "CLOSED" }),
  item({ key: "task:7", day: "2026-11-10", title: "回覆廠商", task_id: 7, assignee_name: "小王" }),
  item({
    key: "occurrence:3:2026-11-10",
    day: "2026-11-10",
    title: "叫牛奶",
    series_id: 3,
    occurrence_on: "2026-11-10",
  }),
  item({ key: "task:8", day: "2026-11-10", title: "補發票紙", task_id: 8, status: "DONE" }),
];

type Call = { url: string; method: string; body: string };
let calls: Call[] = [];

function stubFetch(extra?: (url: string, method: string, body: string) => Response | null) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      const body = input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
      calls.push({ url, method, body });
      const custom = extra?.(url, method, body);
      if (custom) return custom;
      if (url.includes("/auth/me")) return json({ id: 1, role: "MANAGER", store_id: 1 });
      if (url.includes("/agenda/items")) return json({ start: "2026-11-01", end: "2026-12-12", items: ITEMS });
      if (url.includes("/agenda/task-series") && method === "GET") return json([]);
      if (url.includes("/agenda/tasks/undated")) return json([]);
      if (url.includes("/staff-members")) return json([{ id: 2, name: "小王", user_id: null, is_active: true }]);
      if (url.includes("/agenda/today"))
        return json({
          today: "2026-11-10",
          due_today: [],
          overdue: [],
          upcoming: [],
          events: [],
          campaigns: [],
          consignment_pending_count: 0,
          badge_count: 0,
        });
      throw new Error(`unmatched fetch: ${method} ${url}`);
    }),
  );
}

function renderPage() {
  setToken(fakeJwt({ sub: "1", role: "MANAGER", store_id: 1 }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<CalendarPage />, { wrapper: Wrapper });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-10T02:00:00Z"));
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  clearToken();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("/calendar 行事曆", () => {
  it("月曆查整個六週；當天清單列出全部（做完的排最後），格子預設不放已完成", async () => {
    stubFetch();
    renderPage();

    const day = await screen.findByRole("complementary", { name: "11/10（二） 的項目" });
    expect(await within(day).findByText("回覆廠商")).toBeTruthy();
    const titles = within(day)
      .getAllByRole("listitem")
      .map((li) => li.querySelector(".agenda-row-title")?.textContent);
    expect(titles).toEqual(["回覆廠商", "叫牛奶", "補發票紙"]);

    const todayCell = screen.getByRole("button", { name: "11/10（二），2 項" });
    expect(within(todayCell).queryByText("補發票紙")).toBeNull();

    const query = calls.find((c) => c.url.includes("/agenda/items"))!;
    expect(query.url).toContain("start=2026-11-01");
    expect(query.url).toContain("end=2026-12-12");

    // 11/9 的格子有公休
    const cell = screen.getByRole("button", { name: "11/9（一），1 項" });
    expect(within(cell).getByText("公休")).toBeTruthy();
  });

  it("勾選單次待辦走 task_id；勾選還沒建列的重複待辦走規則＋日期", async () => {
    stubFetch((url, method) =>
      method === "POST" && url.includes("/status") ? json({ id: 99, status: "DONE" }) : null,
    );
    renderPage();
    const user = userEvent.setup();
    const day = await screen.findByRole("complementary", { name: "11/10（二） 的項目" });

    await user.click(await within(day).findByRole("checkbox", { name: "回覆廠商" }));
    await user.click(within(day).getByRole("checkbox", { name: "叫牛奶" }));

    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(2));
    const posts = calls.filter((c) => c.method === "POST");
    expect(posts[0].url).toContain("/agenda/tasks/7/status");
    expect(JSON.parse(posts[0].body)).toEqual({ status: "DONE" });
    expect(posts[1].url).toContain("/agenda/task-series/3/occurrences/2026-11-10/status");
  });

  it("按下勾選立刻打勾，不等伺服器回應", async () => {
    // 送出後一直不回應：畫面也要先打勾，不能讓人以為按了沒反應。
    stubFetch((url, method) =>
      method === "POST" && url.includes("/status") ? (new Promise(() => {}) as never) : null,
    );
    renderPage();
    const user = userEvent.setup();
    const day = await screen.findByRole("complementary", { name: "11/10（二） 的項目" });
    const box = await within(day).findByRole("checkbox", { name: "回覆廠商" });
    await user.click(box);
    expect((within(day).getByRole("checkbox", { name: /回覆廠商/ }) as HTMLInputElement).checked).toBe(
      true,
    );
  });

  it("「月曆格子也顯示已完成」打開後，格子裡看得到做完的", async () => {
    stubFetch();
    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("button", { name: "11/10（二），2 項" });

    await user.click(screen.getByRole("checkbox", { name: "月曆格子也顯示已完成" }));
    const cell = await screen.findByRole("button", { name: "11/10（二），3 項" });
    expect(within(cell).getByText("補發票紙")).toBeTruthy();
    // 篩選記在這台裝置上
    expect(window.localStorage.getItem("lu-camp.calendar.filters")).toContain('"showDone":true');
  });

  it("關掉「事件」就看不到公休", async () => {
    stubFetch();
    renderPage();
    const user = userEvent.setup();
    const cell = await screen.findByRole("button", { name: "11/9（一），1 項" });
    expect(within(cell).getByText("公休")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "事件" }));
    expect(await screen.findByRole("button", { name: "11/9（一），0 項" })).toBeTruthy();
  });

  it("新增單次待辦：帶當天日期、負責人與提前天數", async () => {
    stubFetch((url, method) =>
      method === "POST" && url.endsWith("/agenda/tasks") ? json({ id: 50 }, 201) : null,
    );
    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("complementary", { name: "11/10（二） 的項目" });

    await user.click(screen.getByRole("button", { name: "＋ 新增待辦" }));
    const dialog = await screen.findByRole("dialog", { name: "新增待辦" });
    await user.type(within(dialog).getByLabelText("要做什麼"), "盤點帳篷");
    await user.selectOptions(await within(dialog).findByLabelText("負責人（選填）"), "2");
    await user.click(within(dialog).getByRole("button", { name: "儲存" }));

    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/agenda/tasks"))).toBe(true),
    );
    const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/agenda/tasks"))!;
    expect(JSON.parse(post.body)).toEqual({
      title: "盤點帳篷",
      note: null,
      due_time: null,
      assignee_staff_id: 2,
      manager_only: false,
      lead_days: 3,
      due_on: "2026-11-10",
    });
  });

  it("新增重複待辦：選每週幾，送到重複待辦", async () => {
    stubFetch((url, method) =>
      method === "POST" && url.endsWith("/agenda/task-series") ? json({ id: 9 }, 201) : null,
    );
    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("complementary", { name: "11/10（二） 的項目" });

    await user.click(screen.getByRole("button", { name: "＋ 新增待辦" }));
    const dialog = await screen.findByRole("dialog", { name: "新增待辦" });
    await user.type(within(dialog).getByLabelText("要做什麼"), "叫牛奶");
    await user.click(within(dialog).getByRole("radio", { name: "每週" }));
    // 沒選星期就存：先擋下
    await user.click(within(dialog).getByRole("button", { name: "儲存" }));
    expect((await within(dialog).findAllByText("每週重複要選星期幾")).length).toBeGreaterThan(0);

    await user.click(within(dialog).getByRole("button", { name: "週一" }));
    await user.click(within(dialog).getByRole("button", { name: "週四" }));
    expect(within(dialog).getByText("每週一、四")).toBeTruthy();
    await user.click(within(dialog).getByRole("button", { name: "儲存" }));

    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/agenda/task-series"))).toBe(
        true,
      ),
    );
    const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/agenda/task-series"))!;
    expect(JSON.parse(post.body)).toMatchObject({
      title: "叫牛奶",
      starts_on: "2026-11-10",
      recurrence: { kind: "WEEKLY", weekdays: [0, 3], until: null },
    });
  });

  it("換月時右邊跟著選新月份的第一天；換回本月選今天（Codex 1A 第一輪 P2）", async () => {
    stubFetch();
    renderPage();
    const user = userEvent.setup();
    await screen.findByRole("complementary", { name: "11/10（二） 的項目" });

    await user.click(screen.getByRole("button", { name: "下個月" }));
    expect(await screen.findByRole("complementary", { name: "12/1（二） 的項目" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "上個月" }));
    expect(await screen.findByRole("complementary", { name: "11/10（二） 的項目" })).toBeTruthy();
  });

  it("沒有日期的待辦：做完的收在「做完的 N 筆」，點開可以取消打勾（Codex 1A 第一輪 P2）", async () => {
    const undated = (id: number, title: string, status: "OPEN" | "DONE") => ({
      id,
      title,
      note: null,
      due_on: null,
      due_time: null,
      assignee_staff_id: null,
      assignee_name: null,
      manager_only: false,
      link_href: null,
      lead_days: 3,
      series_id: null,
      occurrence_on: null,
      status,
      resolved_at: status === "DONE" ? "2026-11-09T02:00:00Z" : null,
      resolved_by_name: status === "DONE" ? "dev-manager" : null,
      created_at: "2026-11-01T02:00:00Z",
    });
    stubFetch((url, method) => {
      if (url.includes("/agenda/tasks/undated")) {
        return url.includes("include_resolved=true")
          ? json([undated(31, "整理倉庫", "OPEN"), undated(32, "換濾心", "DONE")])
          : json([undated(31, "整理倉庫", "OPEN")]);
      }
      if (method === "POST" && url.includes("/agenda/tasks/32/status")) {
        return json(undated(32, "換濾心", "OPEN"));
      }
      return null;
    });
    renderPage();
    const user = userEvent.setup();

    const card = (await screen.findByText("沒有日期的待辦")).closest(".card") as HTMLElement;
    await within(card).findByText("整理倉庫");
    expect(within(card).queryByText("換濾心")).toBeNull();

    await user.click(within(card).getByRole("button", { name: "做完的 1 筆" }));
    const box = within(card).getByRole("checkbox", { name: "換濾心" }) as HTMLInputElement;
    expect(box.checked).toBe(true);
    await user.click(box);
    await waitFor(() =>
      expect(
        calls.some((c) => c.method === "POST" && c.url.includes("/agenda/tasks/32/status")),
      ).toBe(true),
    );
    const post = calls.find((c) => c.method === "POST" && c.url.includes("/agenda/tasks/32/status"))!;
    expect(JSON.parse(post.body)).toEqual({ status: "OPEN" });
  });

  it("點重複待辦的那一次，打開「只改這一次」", async () => {
    stubFetch();
    renderPage();
    const user = userEvent.setup();
    const day = await screen.findByRole("complementary", { name: "11/10（二） 的項目" });

    await user.click(await within(day).findByRole("button", { name: "叫牛奶" }));
    const dialog = await screen.findByRole("dialog", { name: "叫牛奶" });
    expect(within(dialog).getByText(/在這裡改只影響這一次/)).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "這次不用做" })).toBeTruthy();
  });
});
