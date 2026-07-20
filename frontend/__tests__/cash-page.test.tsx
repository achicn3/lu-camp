// @vitest-environment jsdom
// /cash 現金對帳頁測試：開帳/結帳對帳/手動調整角色顯示/金額驗證。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import CashPage from "@/app/(authed)/cash/page";
import { clearToken, setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function loginAs(role: "MANAGER" | "CLERK") {
  setToken(fakeJwt({ sub: "1", role, store_id: 1 }));
}

const OPEN_SESSION = {
  id: 9,
  store_id: 1,
  status: "OPEN",
  opening_float: "1000",
  opened_by: 1,
  opened_at: "2026-06-12T09:00:00Z",
  closed_at: null,
  closed_by: null,
  counted_amount: null,
  expected_amount: null,
  variance: null,
};

type FetchRoute = (url: string, init?: RequestInit) => Response | null;

function stubFetch(route: FetchRoute) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      const body =
        input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
      const resp = route(url, { method, body } as RequestInit);
      if (resp) return resp;
      throw new Error(`unmatched fetch: ${method} ${url}`);
    }),
  );
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<CashPage />, { wrapper });
}

afterEach(() => {
  cleanup();
  clearToken();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("/cash", () => {
  it("未開帳：顯示開帳表單；送出零用金後進入開帳中狀態", async () => {
    loginAs("CLERK");
    let opened = false;
    stubFetch((url, init) => {
      if (url.includes("/cash-sessions/current")) {
        return json(opened ? OPEN_SESSION : null);
      }
      if (url.includes("/cash-sessions/open") && init?.method === "POST") {
        opened = true;
        return json(OPEN_SESSION, 201);
      }
      return null;
    });
    renderPage();
    const input = await screen.findByLabelText("開帳零用金");
    await userEvent.type(input, "1000");
    await userEvent.click(screen.getByRole("button", { name: "開帳" }));
    expect(await screen.findByText("開帳中")).toBeDefined();
    expect(screen.getByText("1,000")).toBeDefined();
  });

  it("開帳金額非法（非整數）：擋送出並顯示錯誤", async () => {
    loginAs("CLERK");
    const calls: string[] = [];
    stubFetch((url, init) => {
      if (url.includes("/cash-sessions/current")) return json(null);
      if (init?.method === "POST") calls.push(url);
      return json(OPEN_SESSION, 201);
    });
    renderPage();
    const input = await screen.findByLabelText("開帳零用金");
    await userEvent.type(input, "12.5");
    await userEvent.click(screen.getByRole("button", { name: "開帳" }));
    expect(await screen.findByText(/請輸入整數金額/)).toBeDefined();
    expect(calls).toHaveLength(0);
  });

  it("開帳零用金輸入框不接受科學記號字元", async () => {
    loginAs("CLERK");
    stubFetch((url) =>
      url.includes("/cash-sessions/current") ? json(null) : null,
    );
    renderPage();

    const input = await screen.findByLabelText("開帳零用金");
    await userEvent.type(input, "1e3");

    expect(input).toHaveProperty("value", "13");
    expect(screen.getByText(/不可使用科學記號/)).toBeDefined();
  });

  it("開帳中：結帳輸入實點金額 → 顯示應有/實點/差異", async () => {
    loginAs("CLERK");
    stubFetch((url, init) => {
      if (url.includes("/cash-sessions/current")) return json(OPEN_SESSION);
      if (url.includes("/cash-sessions/9/close") && init?.method === "POST") {
        return json({
          ...OPEN_SESSION,
          status: "CLOSED",
          counted_amount: "5100",
          expected_amount: "5200",
          variance: "-100",
        });
      }
      return null;
    });
    renderPage();
    const counted = await screen.findByLabelText("實點金額");
    await userEvent.type(counted, "5100");
    await userEvent.click(screen.getByRole("button", { name: "結帳" }));
    expect(await screen.findByText("已結帳")).toBeDefined();
    expect(screen.getByText("5,200")).toBeDefined(); // 應有
    expect(screen.getByText("5,100")).toBeDefined(); // 實點
    expect(screen.getByText("-100")).toBeDefined(); // 差異
  });

  it("結帳成功後快取即失效：重新開帳顯示開帳表單、不殘留 OPEN 狀態", async () => {
    loginAs("CLERK");
    let closed = false;
    stubFetch((url, init) => {
      if (url.includes("/cash-sessions/current")) return json(closed ? null : OPEN_SESSION);
      if (url.includes("/cash-sessions/9/close") && init?.method === "POST") {
        closed = true;
        return json({
          ...OPEN_SESSION,
          status: "CLOSED",
          counted_amount: "1000",
          expected_amount: "1000",
          variance: "0",
        });
      }
      return null;
    });
    renderPage();
    await userEvent.type(await screen.findByLabelText("實點金額"), "1000");
    await userEvent.click(screen.getByRole("button", { name: "結帳" }));
    await screen.findByText("已結帳");
    await userEvent.click(screen.getByRole("button", { name: "重新開帳" }));
    expect(await screen.findByLabelText("開帳零用金")).toBeDefined();
    expect(screen.queryByText("開帳中")).toBeNull();
  });

  it("MANAGER 看得到手動調整並可送出；含事由", async () => {
    loginAs("MANAGER");
    const bodies: string[] = [];
    stubFetch((url, init) => {
      if (url.includes("/cash-sessions/current")) return json(OPEN_SESSION);
      if (url.includes("/movements") && init?.method === "POST") {
        bodies.push(String(init.body));
        return json({ id: 1, session_id: 9, store_id: 1, type: "MANUAL_ADJUST" }, 201);
      }
      if (url.includes("/movements")) return json([]);
      return null;
    });
    renderPage();
    const amount = await screen.findByLabelText("調整金額（可負）");
    await userEvent.type(amount, "-200");
    await userEvent.type(screen.getByLabelText("事由"), "找錯錢回沖");
    await userEvent.click(screen.getByRole("button", { name: "送出調整" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    const parsed = JSON.parse(bodies[0]) as Record<string, unknown>;
    expect(parsed.type).toBe("MANUAL_ADJUST");
    expect(parsed.amount).toBe("-200");
    expect(parsed.note).toBe("找錯錢回沖");
  });

  it("開帳中顯示手動調整清單，包含正負金額與事由", async () => {
    loginAs("CLERK");
    stubFetch((url) => {
      if (url.includes("/cash-sessions/current")) return json(OPEN_SESSION);
      if (url.includes("/movements")) {
        return json([
          {
            id: 12,
            store_id: 1,
            session_id: 9,
            type: "MANUAL_ADJUST",
            amount: "300",
            note: "補充找零金",
            ref_type: "manual",
            ref_id: null,
            created_at: "2026-06-12T10:30:00Z",
          },
          {
            id: 11,
            store_id: 1,
            session_id: 9,
            type: "MANUAL_ADJUST",
            amount: "-50",
            note: "更正找零差額",
            ref_type: "manual",
            ref_id: null,
            created_at: "2026-06-12T10:00:00Z",
          },
          {
            id: 10,
            store_id: 1,
            session_id: 9,
            type: "SALE_IN",
            amount: "1000",
            note: null,
            ref_type: "sale",
            ref_id: 88,
            created_at: "2026-06-12T09:30:00Z",
          },
        ]);
      }
      return null;
    });

    renderPage();

    const heading = await screen.findByRole("heading", { name: "本班調整紀錄" });
    const history = heading.closest("section");
    expect(history).not.toBeNull();
    expect(await within(history as HTMLElement).findByText("+300")).toBeDefined();
    expect(within(history as HTMLElement).getByText("補充找零金")).toBeDefined();
    expect(within(history as HTMLElement).getByText("-50")).toBeDefined();
    expect(within(history as HTMLElement).getByText("更正找零差額")).toBeDefined();
    expect(within(history as HTMLElement).queryByText("1,000")).toBeNull();
  });

  it("CLERK 看不到手動調整（前端隱藏；後端仍驗權）", async () => {
    loginAs("CLERK");
    stubFetch((url) => (url.includes("/cash-sessions/current") ? json(OPEN_SESSION) : null));
    renderPage();
    await screen.findByText("開帳中");
    expect(screen.queryByLabelText("調整金額（可負）")).toBeNull();
  });
});
