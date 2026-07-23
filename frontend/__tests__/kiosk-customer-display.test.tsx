// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import KioskPage from "@/app/kiosk/page";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

class FakeEventSource extends EventTarget {
  static instances: FakeEventSource[] = [];
  readonly url: string;
  readonly withCredentials: boolean;

  constructor(url: string | URL, init?: EventSourceInit) {
    super();
    this.url = String(url);
    this.withCredentials = init?.withCredentials === true;
    FakeEventSource.instances.push(this);
  }

  close = vi.fn();
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<KioskPage />, { wrapper: Wrapper });
}

beforeEach(() => {
  window.localStorage.clear();
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("/kiosk 客顯", () => {
  it("以裝置 cookie 登入並顯示一次性配對碼，不保存 KIOSK bearer token", async () => {
    const requests: Request[] = [];
    let loggedIn = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(input);
        requests.push(request);
        if (request.url.endsWith("/api/v1/kiosk/device")) {
          return loggedIn
            ? json({
                device_id: 8,
                label: "收銀台客顯",
                pairing_code: "482913",
                pairing_code_expires_at: "2026-07-24T10:05:00Z",
                paired_terminal: null,
              })
            : json({ detail: "未登入" }, 401);
        }
        if (request.url.endsWith("/api/v1/kiosk/device-sessions")) {
          loggedIn = true;
          return json(
            {
              device_id: 8,
              label: "收銀台客顯",
              csrf_token: "csrf-token-at-least-thirty-two-characters",
              pairing_code: "482913",
              pairing_code_expires_at: "2026-07-24T10:05:00Z",
              paired_terminal: null,
            },
            201,
          );
        }
        throw new Error(`unmatched fetch ${request.method} ${request.url}`);
      }),
    );
    const user = userEvent.setup();
    renderPage();

    await user.type(await screen.findByLabelText("帳號"), "kiosk");
    await user.type(screen.getByLabelText("密碼"), "secret");
    await user.type(screen.getByLabelText("裝置名稱"), "收銀台客顯");
    await user.click(screen.getByRole("button", { name: "啟用裝置" }));

    expect(await screen.findByText("482913")).toBeTruthy();
    expect(screen.getByText(/請在 POS 輸入配對碼/)).toBeTruthy();
    const loginRequest = requests.find((r) =>
      r.url.endsWith("/api/v1/kiosk/device-sessions"),
    );
    expect(loginRequest?.credentials).toBe("include");
    expect(window.localStorage.getItem("lu-camp.kiosk.csrf")).toContain("csrf-token");
    expect(window.localStorage.getItem("lu-camp.auth.token")).toBeNull();
  });

  it("配對後只渲染後端快照、無會員時不顯示會員區，總額固定在底部", async () => {
    window.localStorage.setItem(
      "lu-camp.kiosk.csrf",
      "csrf-token-at-least-thirty-two-characters",
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(input);
        if (request.url.endsWith("/api/v1/kiosk/device")) {
          return json({
            device_id: 8,
            label: "收銀台客顯",
            pairing_code: null,
            pairing_code_expires_at: null,
            paired_terminal: { id: 3, name: "主櫃檯" },
          });
        }
        if (request.url.endsWith("/api/v1/kiosk/cart/current")) {
          return json({
            id: 21,
            status: "DRAFT",
            revision: 4,
            pos_terminal_id: 3,
            kiosk_device_id: 8,
            snapshot: {
              content_version: "cart-v1",
              items: [
                {
                  item_key: "CATALOG:6",
                  line_type: "CATALOG",
                  name: "瓦斯罐三入組",
                  qty: 2,
                  unit_price: "120",
                  original_unit_price: null,
                  discount_amount: "0",
                  line_total: "240",
                },
              ],
              total: "240",
              discount_total: "0",
              campaign_name: null,
              member: null,
              tenders: [{ tender_type: "CASH", amount: "240" }],
            },
            changes: [
              {
                type: "QUANTITY_CHANGED",
                item_key: "CATALOG:6",
                name: "瓦斯罐三入組",
                from_qty: 1,
                to_qty: 2,
              },
            ],
            created_at: "2026-07-24T10:00:00Z",
            updated_at: "2026-07-24T10:01:00Z",
          });
        }
        if (request.url.endsWith("/api/v1/kiosk/tasks/current")) return json(null);
        if (request.url.endsWith("/api/v1/kiosk/heartbeat")) {
          return json({ online: true, last_seen_at: "2026-07-24T10:01:00Z" });
        }
        throw new Error(`unmatched fetch ${request.method} ${request.url}`);
      }),
    );

    renderPage();

    expect((await screen.findAllByText("瓦斯罐三入組")).length).toBeGreaterThan(0);
    expect(screen.getByText("1 → 2")).toBeTruthy();
    expect(screen.queryByText("會員")).toBeNull();
    const total = screen.getByTestId("kiosk-total-bar");
    expect(total.classList.contains("kiosk-cart-total")).toBe(true);
    expect(total.textContent).toContain("$240");
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(FakeEventSource.instances[0].withCredentials).toBe(true);
  });
});
