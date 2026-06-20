// @vitest-environment jsdom
// /consignment 寄售付款工作台：列表、開帳狀態、付款確認與 Idempotency-Key。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

vi.mock("@/lib/uuid", () => ({
  newIdempotencyKey: () => "idem-consignment-pay",
}));

import ConsignmentPage from "@/app/(authed)/consignment/page";
import { clearToken, setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function loginAs(role: "MANAGER" | "CLERK") {
  setToken(fakeJwt({ sub: "1", role, store_id: 1 }));
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const OPEN_SESSION = {
  id: 9,
  store_id: 1,
  status: "OPEN",
  opening_float: "1000",
  opened_by: 1,
  opened_at: "2026-06-19T09:00:00Z",
  closed_at: null,
  closed_by: null,
  counted_amount: null,
  expected_amount: null,
  variance: null,
};

const PENDING_SETTLEMENT = {
  id: 11,
  store_id: 1,
  serialized_item_id: 101,
  sale_id: 501,
  gross: "3000",
  commission_pct: 40,
  commission_amount: "1200",
  payout_amount: "1800",
  status: "PENDING",
  paid_at: null,
  paid_by: null,
  reclaim_needed: false,
  created_at: "2026-06-19T10:00:00Z",
  item_code: "CON-001",
  item_name: "寄售帳篷",
  consignor_id: 7,
  consignor_name: "林小露",
  consignor_phone: "0912-000-111",
  sale_created_at: "2026-06-19T10:00:00Z",
};

const PAID_RECLAIM_SETTLEMENT = {
  ...PENDING_SETTLEMENT,
  id: 12,
  sale_id: 502,
  item_code: "CON-002",
  item_name: "寄售睡袋",
  payout_amount: "900",
  status: "PAID",
  paid_at: "2026-06-19T12:00:00Z",
  paid_by: 2,
  reclaim_needed: true,
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
      const headers = input instanceof Request ? input.headers : new Headers(init?.headers);
      const resp = route(url, { method, body, headers } as RequestInit);
      if (resp) return resp;
      throw new Error(`unmatched fetch: ${method} ${url}`);
    }),
  );
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<ConsignmentPage />, { wrapper });
}

afterEach(() => {
  cleanup();
  clearToken();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("/consignment", () => {
  it("shows pending payouts and drawer-open state", async () => {
    loginAs("CLERK");
    stubFetch((url) => {
      if (url.includes("/cash-sessions/current")) return json(OPEN_SESSION);
      if (url.includes("/consignment/settlements")) return json([PENDING_SETTLEMENT]);
      return null;
    });

    renderPage();

    expect(await screen.findByText("林小露")).toBeTruthy();
    expect(screen.getByText("寄售付款")).toBeTruthy();
    expect(screen.getByText("開帳中")).toBeTruthy();
    expect(screen.getByText("寄售帳篷")).toBeTruthy();
    expect(screen.getByText("CON-001")).toBeTruthy();
    expect(screen.getAllByText("1,800").length).toBeGreaterThan(0);
    expect((screen.getByRole("button", { name: "付款" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("disables payout when no cash session is open", async () => {
    loginAs("CLERK");
    stubFetch((url) => {
      if (url.includes("/cash-sessions/current")) return json(null);
      if (url.includes("/consignment/settlements")) return json([PENDING_SETTLEMENT]);
      return null;
    });

    renderPage();

    expect(await screen.findByText("林小露")).toBeTruthy();
    expect(screen.getByText("未開帳")).toBeTruthy();
    expect((screen.getByRole("button", { name: "付款" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("請先到現金對帳開帳後再付款。")).toBeTruthy();
  });

  it("confirms payout with Idempotency-Key and refreshes the list", async () => {
    loginAs("CLERK");
    const calls: { url: string; key: string | null }[] = [];
    let paid = false;
    stubFetch((url, init) => {
      if (url.includes("/cash-sessions/current")) return json(OPEN_SESSION);
      if (url.includes("/consignment/settlements/11/pay") && init?.method === "POST") {
        calls.push({
          url,
          key: (init.headers as Headers).get("Idempotency-Key"),
        });
        paid = true;
        return json({ ...PENDING_SETTLEMENT, status: "PAID", paid_at: "2026-06-19T13:00:00Z" });
      }
      if (url.includes("/consignment/settlements")) {
        return json(paid ? [] : [PENDING_SETTLEMENT]);
      }
      return null;
    });

    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "付款" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/確認支付/)).toBeTruthy();
    await userEvent.click(within(dialog).getByRole("button", { name: "確認付款" }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].key).toBe("idem-consignment-pay");
    expect(await screen.findByText("目前沒有待付款的寄售結算。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "付款" })).toBeNull();
  });

  it("shows reclaim-needed paid settlements on the paid tab", async () => {
    loginAs("MANAGER");
    stubFetch((url) => {
      if (url.includes("/cash-sessions/current")) return json(OPEN_SESSION);
      if (url.includes("/consignment/settlements")) return json([PAID_RECLAIM_SETTLEMENT]);
      return null;
    });

    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "已付款" }));
    expect(await screen.findByText("寄售睡袋")).toBeTruthy();
    expect(screen.getByText("需追回")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "付款" })).toBeNull();
  });
});
