// @vitest-environment jsdom
// 號碼牌列印（2026-09-05 裁示）：候位中的每一列可補印一張給客人拿。
//
// 兩個重點：
//   1. **走收據機，不走發票機**——去向由代理端的路由決定，前端只要送對端點。
//      送錯台不只是跑錯紙：兩台字型 ROM 不同（Big5 vs GB18030），中文會整捲亂碼。
//   2. **列印失敗不擋叫號作業**——號碼早就配出去了、清單上也看得到，紙只是輔助。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import CallTicketsPage from "@/app/(authed)/call-tickets/page";
import { todayInTaipei } from "@/features/call-tickets/callTickets";
import { setToken } from "@/lib/token";

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

// 用「今天」而非寫死日期：ticketLabel 會對今天以外的加日期前綴（`9/5 #7`），
// 寫死的話跨日跑測試就會紅——測的是列印，不是日曆。
const TODAY = todayInTaipei();
const TICKET = {
  id: 5,
  store_id: 1,
  ticket_date: TODAY,
  ticket_no: 7,
  name: "王小明",
  link: null,
  note: null,
  status: "WAITING",
  created_at: "2026-09-05T01:30:00Z",
};

function renderPage() {
  setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<CallTicketsPage />, { wrapper: Wrapper });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("號碼牌列印", () => {
  it("送到代理端的 /print/call-ticket，帶畫面上顯示的號碼", async () => {
    let printBody: Record<string, unknown> | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        const body =
          input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
        if (url.includes("/print/call-ticket")) {
          printBody = JSON.parse(body);
          return json({ ok: true });
        }
        if (url.includes("/api/v1/call-tickets")) return json([TICKET]);
        return json({});
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("王小明")).toBeTruthy());

    await user.click(screen.getByRole("button", { name: "列印號碼牌 7" }));
    await waitFor(() => expect(printBody).not.toBeNull());
    expect(printBody).toMatchObject({
      store_id: 1,
      ticket_no: 7,
      label: "#7",
      name: "王小明",
      created_at: "2026-09-05T01:30:00Z",
    });
  });

  it("列印失敗只提示、不擋叫號作業（號碼已經配出去了）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("/print/call-ticket")) throw new TypeError("Failed to fetch");
        if (url.includes("/api/v1/call-tickets")) return json([TICKET]);
        return json({});
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("王小明")).toBeTruthy());

    await user.click(screen.getByRole("button", { name: "列印號碼牌 7" }));
    await waitFor(() => expect(screen.getByText(/號碼牌列印失敗/)).toBeTruthy());
    // 那張單仍在候位中、「完成」照樣可按——作業沒有被列印卡住
    expect(screen.getByRole("button", { name: "完成叫號 7" })).toHaveProperty(
      "disabled",
      false,
    );
  });

  it("已完成的不顯示列印鍵（印了也沒意義）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("/api/v1/call-tickets"))
          return json([{ ...TICKET, status: "DONE" }]);
        return json({});
      }),
    );
    renderPage();
    await waitFor(() => expect(screen.getByText("王小明")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "列印號碼牌 7" })).toBeNull();
  });
});

describe("歷史檢視：分頁與日期篩選", () => {
  function stubList(capture: string[]) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("/api/v1/call-tickets")) {
          capture.push(url);
          return json([TICKET]);
        }
        return json({});
      }),
    );
  }

  it("候位中不帶 offset、不帶日期（一頁看完）", async () => {
    const urls: string[] = [];
    stubList(urls);
    renderPage();
    await waitFor(() => expect(screen.getByText("王小明")).toBeTruthy());
    const listed = urls.filter((u) => u.includes("/call-tickets"));
    expect(listed.some((u) => u.includes("include_done=false"))).toBe(true);
    expect(listed.every((u) => !u.includes("ticket_date="))).toBe(true);
  });

  it("勾顯示已完成 → 帶 include_done 與每頁 50 筆，翻頁帶 offset", async () => {
    const urls: string[] = [];
    stubList(urls);
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("王小明")).toBeTruthy());

    await user.click(screen.getByRole("checkbox"));
    await waitFor(() =>
      expect(urls.some((u) => u.includes("include_done=true") && u.includes("limit=50"))).toBe(
        true,
      ),
    );
    // 滿頁才會出現「下一頁」；這裡只回 1 筆，故不應出現分頁控制
    expect(screen.queryByRole("button", { name: /下一頁/ })).toBeNull();
  });

  it("**日期留空不得送出 ticket_date**（送空字串會被後端當格式錯誤而 422）", async () => {
    const urls: string[] = [];
    stubList(urls);
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("王小明")).toBeTruthy());
    await user.click(screen.getByRole("checkbox"));
    await waitFor(() => expect(urls.some((u) => u.includes("include_done=true"))).toBe(true));

    expect(urls.every((u) => !u.includes("ticket_date="))).toBe(true);
  });

  it("**取消勾選後不得殘留日期篩選**——候位清單會顯示錯的一天卻沒有控制可以清", async () => {
    // 日期輸入框只在「顯示已完成」時渲染。若查詢條件沒綁 showDone、取消勾選也不清值，
    // 那個看不見的日期會繼續套在候位清單上：畫面說「目前沒有人在候位」，實際上有人在等。
    const urls: string[] = [];
    stubList(urls);
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("王小明")).toBeTruthy());

    await user.click(screen.getByRole("checkbox"));
    const dateInput = await screen.findByLabelText("只看某一天");
    await user.type(dateInput, "2026-09-01");
    await waitFor(() => expect(urls.some((u) => u.includes("ticket_date=2026-09-01"))).toBe(true));

    urls.length = 0;
    await user.click(screen.getByRole("checkbox")); // 取消勾選 → 回候位檢視
    await waitFor(() => expect(urls.some((u) => u.includes("include_done=false"))).toBe(true));
    expect(urls.every((u) => !u.includes("ticket_date="))).toBe(true);
  });

  it("選了日期 → 帶 ticket_date 並回到第一頁", async () => {
    const urls: string[] = [];
    stubList(urls);
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText("王小明")).toBeTruthy());
    await user.click(screen.getByRole("checkbox"));
    const dateInput = await screen.findByLabelText("只看某一天");
    await user.type(dateInput, "2026-09-05");

    await waitFor(() =>
      expect(
        urls.some((u) => u.includes("ticket_date=2026-09-05") && u.includes("offset=0")),
      ).toBe(true),
    );
  });
});
