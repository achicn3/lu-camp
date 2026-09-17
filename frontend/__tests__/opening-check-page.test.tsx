// @vitest-environment jsdom
// /opening-check 開店前檢查：自動項目由系統判定、可略過；自訂項目可打勾；
// 代理連不到時不得謊報全部完成。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/opening-check",
}));

import OpeningCheckPage from "@/app/(authed)/opening-check/page";
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

const TODAY = {
  business_date: "2026-09-17",
  cash_session_open: false,
  items: [{ id: 5, label: "零錢補足", href: "/cash", done: false }],
  skipped_keys: [] as string[],
  completed: false,
};

const DEVICES = {
  devices: [
    {
      id: "ql810w",
      kind: "LABEL_PRINTER",
      model: "Brother QL-810W",
      online: false,
      last_seen: null,
      probe_error: null,
      driver: "real",
    },
    {
      id: "epson-42",
      kind: "RECEIPT_PRINTER",
      model: "EPSON TM-T82",
      online: true,
      last_seen: "2026-09-17T01:00:00Z",
      probe_error: null,
      driver: "real",
    },
  ],
};

type Route = (url: string, method: string, body: string) => Response | null;

function stubFetch(route: Route) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      const body =
        input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
      if (url.includes("/auth/me")) return json({ id: 1, role: "MANAGER", store_id: 1 });
      const resp = route(url, method, body);
      if (resp) return resp;
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
  return render(<OpeningCheckPage />, { wrapper: Wrapper });
}

afterEach(() => {
  cleanup();
  clearToken();
  vi.unstubAllGlobals();
});

describe("/opening-check 開店前檢查", () => {
  it("列出開帳與各裝置；沒過的才有處理按鈕", async () => {
    stubFetch((url) => {
      if (url.includes("/devices/status")) return json(DEVICES);
      if (url.includes("/opening-check/today")) return json(TODAY);
      return null;
    });
    renderPage();

    const cashRow = (await screen.findByText("今日已開帳")).closest("li")!;
    expect(within(cashRow).getByText("待處理")).toBeTruthy();
    expect(within(cashRow).getByRole("link", { name: "去開帳" })).toBeTruthy();

    const online = (await screen.findByText(/收據／發票機/)).closest("li")!;
    expect(within(online).getByText("正常")).toBeTruthy();
    // 綠燈的那列不放按鈕，免得整頁都是鈕
    expect(within(online).queryByRole("button")).toBeNull();
  });

  it("略過送出 key，且不需要填原因", async () => {
    let posted = "";
    stubFetch((url, method, body) => {
      if (url.includes("/opening-check/today/skip") && method === "POST") {
        posted = body;
        return json({ ...TODAY, skipped_keys: ["cash_session"] });
      }
      if (url.includes("/devices/status")) return json(DEVICES);
      if (url.includes("/opening-check/today")) return json(TODAY);
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("今日已開帳");
    await user.click(screen.getAllByRole("button", { name: "今天略過" })[0]);
    await waitFor(() => expect(posted).not.toBe(""));
    expect(JSON.parse(posted)).toEqual({ key: "cash_session" });
  });

  it("勾選自訂項目送出 done", async () => {
    let posted = "";
    stubFetch((url, method, body) => {
      if (url.includes("/opening-check/today/items/5") && method === "POST") {
        posted = body;
        return json({ ...TODAY, items: [{ ...TODAY.items[0], done: true }] });
      }
      if (url.includes("/devices/status")) return json(DEVICES);
      if (url.includes("/opening-check/today")) return json(TODAY);
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByLabelText("零錢補足"));
    await waitFor(() => expect(posted).not.toBe(""));
    expect(JSON.parse(posted)).toEqual({ done: true });
  });

  it("代理連不到時講明看不到機器狀態，且不得顯示今日檢查完成", async () => {
    stubFetch((url) => {
      if (url.includes("/devices/status")) return json({ detail: "boom" }, 500);
      if (url.includes("/opening-check/today")) {
        return json({ ...TODAY, cash_session_open: true, items: [], completed: true });
      }
      return null;
    });
    renderPage();
    expect(await screen.findByText(/連不到店內的列印代理/)).toBeTruthy();
    expect(screen.queryByText("今日檢查完成")).toBeNull();
  });

  it("全部通過時顯示今日檢查完成", async () => {
    stubFetch((url) => {
      if (url.includes("/devices/status")) {
        return json({ devices: [DEVICES.devices[1]] });
      }
      if (url.includes("/opening-check/today")) {
        return json({ ...TODAY, cash_session_open: true, items: [], completed: true });
      }
      return null;
    });
    renderPage();
    expect(await screen.findByText("今日檢查完成")).toBeTruthy();
  });
});
