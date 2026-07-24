// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import KioskPage from "@/app/kiosk/page";

vi.mock("@/app/kiosk/SignatureCanvas", async () => {
  const React = await import("react");
  return {
    SignatureCanvas: React.forwardRef<
      { toBase64(): string; clear(): void },
      { onInkChange: (hasInk: boolean) => void }
    >(function FakeSignatureCanvas({ onInkChange }, ref) {
      React.useImperativeHandle(ref, () => ({
        toBase64: () => "normalized-png-base64",
        clear: () => onInkChange(false),
      }));
      return (
        <button type="button" onClick={() => onInkChange(true)}>
          模擬簽名
        </button>
      );
    }),
  };
});

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
  return { ...render(<KioskPage />, { wrapper: Wrapper }), client };
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
                // 裝置狀態 GET 不保存配對明碼；登入 POST 的仍有效明碼必須留在畫面。
                pairing_code: null,
                pairing_code_expires_at: null,
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
    let cartStatus: "DRAFT" | "PROCESSING" = "DRAFT";
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
            status: cartStatus,
            revision: 4,
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
              {
                type: "DISCOUNT_CHANGED",
                item_key: "TOTAL",
                name: "折扣已重新計算",
                from_qty: null,
                to_qty: null,
              },
            ],
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
    expect(screen.getByText("折扣已重新計算").parentElement?.textContent).toBe(
      "折扣已重新計算，應付總額已更新",
    );
    expect(screen.queryByText("會員")).toBeNull();
    const total = screen.getByTestId("kiosk-total-bar");
    expect(total.classList.contains("kiosk-cart-total")).toBe(true);
    expect(total.textContent).toContain("$240");
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(FakeEventSource.instances[0].withCredentials).toBe(true);
    cartStatus = "PROCESSING";
    FakeEventSource.instances[0].dispatchEvent(new Event("state"));
    expect(await screen.findByText("付款處理中，請稍候")).toBeTruthy();
  });

  it("送出簽名時帶裝置 CSRF token", async () => {
    const csrf = "csrf-token-at-least-thirty-two-characters";
    window.localStorage.setItem("lu-camp.kiosk.csrf", csrf);
    const requests: Request[] = [];
    const task = {
      id: 41,
      store_id: 1,
      kind: "STORE_CREDIT_USE",
      status: "SIGNING",
      contact_id: 7,
      content: {
        content_version: "store-credit-use-v1",
        items: [{ name: "露營燈", qty: 1, unit_price: "1000", line_total: "1000" }],
        sale_total: "1000",
        store_credit_amount: "300",
        remaining_tenders: [{ tender_type: "LINE_PAY", amount: "700" }],
      },
      agreement_title: null,
      agreement_body: null,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(input);
        requests.push(request);
        if (request.url.endsWith("/api/v1/kiosk/device")) {
          return json({
            device_id: 8,
            label: "收銀台客顯",
            pairing_code: null,
            pairing_code_expires_at: null,
            paired_terminal: { id: 3, name: "主櫃檯" },
          });
        }
        if (request.url.endsWith("/api/v1/kiosk/cart/current")) return json(null);
        if (request.url.endsWith("/api/v1/kiosk/tasks/current")) return json(task);
        if (request.url.endsWith("/api/v1/kiosk/heartbeat")) {
          return json({ online: true, last_seen_at: "2026-07-24T10:01:00Z" });
        }
        if (request.url.endsWith("/activity")) return json(task);
        if (request.url.endsWith("/sign")) return json({ ...task, status: "SIGNED" });
        throw new Error(`unmatched fetch ${request.method} ${request.url}`);
      }),
    );
    const user = userEvent.setup();
    const { client } = renderPage();

    await user.click(await screen.findByRole("button", { name: "模擬簽名" }));
    await user.click(screen.getByRole("button", { name: "確認並送出" }));

    await screen.findByText("已完成簽署");
    expect(client.getQueryData(["kiosk", "current"])).toBeUndefined();
    const signRequest = requests.find((request) => request.url.endsWith("/sign"));
    expect(signRequest?.headers.get("X-CSRF-Token")).toBe(csrf);
  });

  it("客顯先實際渲染 PENDING 快照，再送 ACK 進入簽署", async () => {
    const csrf = "csrf-token-at-least-thirty-two-characters";
    window.localStorage.setItem("lu-camp.kiosk.csrf", csrf);
    let resolveAck!: (response: Response) => void;
    const ackResponse = new Promise<Response>((resolve) => {
      resolveAck = resolve;
    });
    const task = {
      id: 42,
      kind: "STORE_CREDIT_USE",
      status: "PENDING",
      content: {
        items: [{ name: "露營燈", qty: 1, unit_price: "1000", line_total: "1000" }],
        total: "1000",
      },
      agreement_title: null,
      agreement_body: null,
    };
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
        if (request.url.endsWith("/api/v1/kiosk/cart/current")) return json(null);
        if (request.url.endsWith("/api/v1/kiosk/tasks/current")) return json(task);
        if (request.url.endsWith("/api/v1/kiosk/heartbeat")) {
          return json({ online: true, last_seen_at: "2026-07-24T10:01:00Z" });
        }
        if (request.url.endsWith("/ack")) return ackResponse;
        if (request.url.endsWith("/activity")) {
          return json({ ...task, status: "SIGNING" });
        }
        throw new Error(`unmatched fetch ${request.method} ${request.url}`);
      }),
    );

    renderPage();

    expect(await screen.findByText("露營燈")).toBeTruthy();
    expect(screen.getByText("正在確認簽署畫面…")).toBeTruthy();
    resolveAck(json({ ...task, status: "SIGNING" }));
  });

  it("簽署仍為 SIGNED 時，PAYMENT_UNCERTAIN 必須蓋過交回鎖並警告勿重複付款", async () => {
    window.localStorage.setItem(
      "lu-camp.kiosk.csrf",
      "csrf-token-at-least-thirty-two-characters",
    );
    window.localStorage.setItem("lu-camp.kiosk-handoff", "1");
    const task = {
      id: 43,
      kind: "STORE_CREDIT_USE",
      status: "SIGNED",
      content: { total: "1000", store_credit_amount: "300" },
      agreement_title: null,
      agreement_body: null,
    };
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
            status: "PAYMENT_UNCERTAIN",
            revision: 8,
            snapshot: {
              content_version: "cart-v1",
              items: [
                {
                  item_key: "SERIALIZED:LAMP1",
                  line_type: "SERIALIZED",
                  name: "露營燈",
                  qty: 1,
                  unit_price: "1000",
                  original_unit_price: null,
                  discount_amount: "0",
                  line_total: "1000",
                },
              ],
              total: "1000",
              discount_total: "0",
              campaign_name: null,
              member: { display_name: "林○試" },
              tenders: [
                { tender_type: "STORE_CREDIT", amount: "300" },
                { tender_type: "LINE_PAY", amount: "700" },
              ],
            },
            changes: [],
            updated_at: "2026-07-24T10:01:00Z",
          });
        }
        if (request.url.endsWith("/api/v1/kiosk/tasks/current")) {
          return json(task);
        }
        if (request.url.endsWith("/api/v1/kiosk/heartbeat")) {
          return json({
            online: true,
            last_seen_at: "2026-07-24T10:01:00Z",
          });
        }
        throw new Error(`unmatched fetch ${request.method} ${request.url}`);
      }),
    );

    renderPage();

    expect(
      await screen.findByText("付款確認中，請勿重複付款"),
    ).toBeTruthy();
    expect(screen.getByText("購物金＋LINE Pay")).toBeTruthy();
    expect(screen.queryByText("已完成簽署")).toBeNull();
  });

  it("成交完成畫面到期後清除舊簽署鎖並回待機", async () => {
    window.localStorage.setItem(
      "lu-camp.kiosk.csrf",
      "csrf-token-at-least-thirty-two-characters",
    );
    window.localStorage.setItem("lu-camp.kiosk-handoff", "1");
    window.localStorage.setItem("lu-camp.kiosk-engaged", "43");
    let cartReads = 0;
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
          cartReads += 1;
          if (cartReads > 1) return json(null);
          return json({
            id: 21,
            status: "COMPLETED",
            revision: 9,
            snapshot: {
              content_version: "cart-v1",
              items: [],
              total: "1000",
              discount_total: "0",
              campaign_name: null,
              member: null,
              tenders: [
                { tender_type: "STORE_CREDIT", amount: "300" },
                { tender_type: "TAIWAN_PAY", amount: "700" },
              ],
            },
            changes: [],
            // 已超過後端完成畫面 TTL，timer 應立即做本機清場並重讀權威狀態。
            updated_at: "2020-01-01T00:00:00Z",
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

    expect(await screen.findByText("露營二手")).toBeTruthy();
    await waitFor(() => {
      expect(window.localStorage.getItem("lu-camp.kiosk-handoff")).toBeNull();
      expect(window.localStorage.getItem("lu-camp.kiosk-engaged")).toBeNull();
    });
    expect(screen.queryByText("已完成簽署")).toBeNull();
  });
});
