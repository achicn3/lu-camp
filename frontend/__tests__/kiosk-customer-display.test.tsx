// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
              pairing_code_expires_at: new Date(Date.now() + 5 * 60_000).toISOString(),
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
                  original_unit_price: "140",
                  discount_amount: "40",
                  line_total: "240",
                },
                ...Array.from({ length: 11 }, (_, index) => ({
                  item_key: `CATALOG:${index + 7}`,
                  line_type: "CATALOG",
                  name: `露營補充品 ${index + 1}`,
                  qty: 1,
                  unit_price: "10",
                  original_unit_price: null,
                  discount_amount: "0",
                  line_total: "10",
                })),
              ],
              total: "350",
              discount_total: "40",
              campaign_name: null,
              member: null,
              tenders: [{ tender_type: "CASH", amount: "350" }],
            },
            changes: [
              {
                type: "ADDED",
                item_key: "CATALOG:7",
                name: "露營補充品 1",
                from_qty: null,
                to_qty: 1,
              },
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
    expect(screen.getAllByText("露營補充品 1").length).toBeGreaterThan(0);
    expect(screen.queryByText("已加入", { exact: false })).toBeNull();
    expect(screen.getByText("1 → 2")).toBeTruthy();
    expect(screen.getByText("折扣已重新計算").parentElement?.textContent).toBe(
      "折扣已重新計算，應付總額已更新",
    );
    expect(screen.getByText("原價 $140")).toBeTruthy();
    expect(screen.getByText("優惠價 $120")).toBeTruthy();
    expect(screen.getByText("折扣 $40")).toBeTruthy();
    expect(screen.queryByText(/本行折抵/)).toBeNull();
    expect(screen.getByText("本次共折扣 $40")).toBeTruthy();
    expect(screen.queryByText("會員")).toBeNull();
    const total = screen.getByTestId("kiosk-total-bar");
    expect(total.classList.contains("kiosk-cart-total")).toBe(true);
    expect(total.textContent).toContain("$350");
    const itemList = screen.getByLabelText("商品明細");
    Object.defineProperties(itemList, {
      clientHeight: { configurable: true, value: 400 },
      scrollHeight: { configurable: true, value: 1200 },
      scrollTop: { configurable: true, value: 0, writable: true },
    });
    fireEvent.scroll(itemList);
    expect(screen.getByText("共 12 個品項 · 向下滑查看更多 ↓")).toBeTruthy();
    expect(itemList.classList.contains("has-scroll-hint")).toBe(true);
    itemList.scrollTop = 800;
    fireEvent.scroll(itemList);
    expect(
      screen.getByText("已顯示全部 12 個品項 · 向上滑可返回 ↑"),
    ).toBeTruthy();
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    expect(FakeEventSource.instances[0].withCredentials).toBe(true);
    cartStatus = "PROCESSING";
    FakeEventSource.instances[0].dispatchEvent(new Event("state"));
    expect(await screen.findByText("付款處理中，請稍候")).toBeTruthy();
    expect(
      screen.getByRole("status", { name: "付款處理中" }),
    ).toBeTruthy();
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
        store_credit_balance_after: "700",
        remaining_tenders: [{ tender_type: "LINE_PAY", amount: "700" }],
        member: { display_name: "林○○試" },
        content_version: "store-credit-signature-v1",
        total: "1000",
        store_credit_amount: "300",
        items: [{ name: "露營燈", qty: 1, unit_price: "1000", line_total: "1000" }],
        store_credit_balance_before: "1000",
        discount_total: "0",
        campaign_name: null,
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

    expect(await screen.findByText("文件版本 v1")).toBeTruthy();
    expect(screen.queryByText("store-credit-signature-v1")).toBeNull();
    expect(
      Array.from(document.querySelectorAll(".kiosk-field-row dt")).map(
        (element) => element.textContent,
      ),
    ).toEqual([
      "合計金額",
      "會員",
      "優惠活動",
      "折扣合計",
      "扣抵前購物金餘額",
      "本次使用購物金",
      "扣抵後購物金餘額",
      "剩餘付款",
    ]);
    await user.click(await screen.findByRole("button", { name: "模擬簽名" }));
    await user.click(screen.getByRole("button", { name: "確認並送出" }));

    await screen.findByText("已完成簽署");
    // 店主裁示：簽完即感謝並自動回待機，不再要求店員輸入帳密交接。
    expect(screen.getByText(/秒後自動回到待機畫面/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /店員解鎖/ })).toBeNull();
    expect(window.localStorage.getItem("lu-camp.kiosk-handoff")).toBeNull();
    // 簽畢即釋放任務釘選：中途重整或倒數結束都能直接接續下一位，不必店員解鎖。
    expect(window.localStorage.getItem("lu-camp.kiosk-engaged")).toBeNull();
    expect(client.getQueryData(["kiosk", "current"])).toBeUndefined();
    const signRequest = requests.find((request) => request.url.endsWith("/sign"));
    expect(signRequest?.headers.get("X-CSRF-Token")).toBe(csrf);
  });

  it("簽署完成倒數結束自動回待機，下一張任務免店員帳密即自動顯示", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const csrf = "csrf-token-at-least-thirty-two-characters";
      window.localStorage.setItem("lu-camp.kiosk.csrf", csrf);
      const signing = {
        id: 51,
        kind: "STORE_CREDIT_USE",
        status: "SIGNING",
        content: {
          total: "1000",
          items: [{ name: "露營燈", qty: 1, unit_price: "1000", line_total: "1000" }],
        },
        agreement_title: null,
        agreement_body: null,
      };
      const nextCustomerTask = {
        id: 52,
        kind: "STORE_CREDIT_USE",
        status: "SIGNING",
        content: {
          total: "2000",
          items: [{ name: "折疊露營椅", qty: 1, unit_price: "2000", line_total: "2000" }],
        },
        agreement_title: null,
        agreement_body: null,
      };
      let currentTask: Record<string, unknown> = signing;
      vi.stubGlobal(
        "fetch",
        vi.fn(async (input: RequestInfo | URL) => {
          const request = input instanceof Request ? input : new Request(input);
          if (request.url.endsWith("/api/v1/kiosk/device")) {
            return json({
              device_id: 8,
              label: "收銀台顧客螢幕",
              pairing_code: null,
              pairing_code_expires_at: null,
              paired_terminal: { id: 3, name: "主櫃檯" },
            });
          }
          if (request.url.endsWith("/api/v1/kiosk/cart/current")) return json(null);
          if (request.url.endsWith("/api/v1/kiosk/tasks/current")) return json(currentTask);
          if (request.url.endsWith("/api/v1/kiosk/heartbeat")) {
            return json({ online: true, last_seen_at: "2026-07-24T10:01:00Z" });
          }
          if (request.url.endsWith("/activity")) return json(currentTask);
          if (request.url.endsWith("/sign")) {
            currentTask = { ...signing, status: "SIGNED" };
            return json(currentTask);
          }
          throw new Error(`unmatched fetch ${request.method} ${request.url}`);
        }),
      );
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      renderPage();

      await user.click(await screen.findByRole("button", { name: "模擬簽名" }));
      await user.click(screen.getByRole("button", { name: "確認並送出" }));
      expect(await screen.findByText("已完成簽署")).toBeTruthy();
      expect(screen.getByText(/10 秒後自動回到待機畫面/)).toBeTruthy();

      // 倒數結束：不需任何店員操作即恢復輪詢；此筆仍為 SIGNED 時只顯示等待訊息。
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(await screen.findByText(/請稍候，店員將完成後續作業/)).toBeTruthy();
      expect(screen.queryByRole("button", { name: /解鎖/ })).toBeNull();

      // 店員推下一位客人的任務：不再需要帳密解鎖，直接顯示。
      currentTask = nextCustomerTask;
      await act(async () => {
        FakeEventSource.instances[0].dispatchEvent(new Event("state"));
        await vi.advanceTimersByTimeAsync(50);
      });
      expect(await screen.findByText("折疊露營椅")).toBeTruthy();
      expect(screen.queryByText("已完成簽署")).toBeNull();
      expect(window.localStorage.getItem("lu-camp.kiosk-handoff")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("曖昧簽署恢復畫面仍要求店員帳密才解鎖", async () => {
    const csrf = "csrf-token-at-least-thirty-two-characters";
    window.localStorage.setItem("lu-camp.kiosk.csrf", csrf);
    // 送出後回應遺失（曖昧）留下的持久簽署鎖：此路徑保留店員確認。
    window.localStorage.setItem("lu-camp.kiosk-signing", "1");
    const task = {
      id: 61,
      kind: "STORE_CREDIT_USE",
      status: "SIGNING",
      content: {
        total: "500",
        items: [{ name: "營釘組", qty: 1, unit_price: "500", line_total: "500" }],
      },
      agreement_title: null,
      agreement_body: null,
    };
    const clerkToken = `h.${btoa(JSON.stringify({ role: "CLERK" }))}.s`;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(input);
        if (request.url.endsWith("/api/v1/kiosk/device")) {
          return json({
            device_id: 8,
            label: "收銀台顧客螢幕",
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
        if (request.url.endsWith("/api/v1/auth/login")) {
          const body = (await request.clone().json()) as { password: string };
          return body.password === "right-pass"
            ? json({ access_token: clerkToken, token_type: "bearer" })
            : json({ detail: "帳號或密碼錯誤" }, 401);
        }
        throw new Error(`unmatched fetch ${request.method} ${request.url}`);
      }),
    );
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("上一筆簽署尚未確認")).toBeTruthy();
    expect(screen.queryByText("營釘組")).toBeNull();
    await user.click(screen.getByRole("button", { name: "店員確認並解鎖" }));
    await user.type(screen.getByLabelText("店員帳號"), "dev-clerk");
    await user.type(screen.getByLabelText("密碼"), "wrong-pass");
    await user.click(screen.getByRole("button", { name: "解鎖" }));
    expect(await screen.findByText("店務員帳密不正確，無法解鎖。")).toBeTruthy();
    expect(screen.getByText("上一筆簽署尚未確認")).toBeTruthy();
    expect(screen.queryByText("營釘組")).toBeNull();

    await user.clear(screen.getByLabelText("密碼"));
    await user.type(screen.getByLabelText("密碼"), "right-pass");
    await user.click(screen.getByRole("button", { name: "解鎖" }));
    expect(await screen.findByText("營釘組")).toBeTruthy();
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

  it("收購切結依賣方與交易分組，並要求明確選擇收款方式", async () => {
    const csrf = "csrf-token-at-least-thirty-two-characters";
    window.localStorage.setItem("lu-camp.kiosk.csrf", csrf);
    const task = {
      id: 44,
      kind: "ACQUISITION_AFFIDAVIT",
      status: "SIGNING",
      content: {
        phone: "0911888777",
        address: "台北市測試路 1 號",
        seller_name: "林客顯測試",
        national_id_masked: "B10****002",
        items: [{ name: "二手睡袋", amount: "400" }],
        store_credit_premium: { rate: "0.1", amount: "440", extra: "40" },
      },
      agreement_title: "二手商品讓售切結書",
      agreement_body: "本人確認上述內容。",
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
        if (request.url.endsWith("/activity")) return json(task);
        throw new Error(`unmatched fetch ${request.method} ${request.url}`);
      }),
    );
    const user = userEvent.setup();
    renderPage();

    const sellerGroup = (await screen.findByRole("heading", {
      name: "賣方資料",
    })).closest("section");
    const transactionGroup = screen
      .getByRole("heading", { name: "收購資料" })
      .closest("section");
    expect(
      Array.from(sellerGroup?.querySelectorAll("dt") ?? []).map(
        (element) => element.textContent,
      ),
    ).toEqual(["姓名", "身分證字號", "電話", "住址"]);
    expect(
      Array.from(transactionGroup?.querySelectorAll("dt") ?? []).map(
        (element) => element.textContent,
      ),
    ).toEqual([]);
    expect(transactionGroup?.textContent).toContain("二手睡袋");
    expect(
      Boolean(
        sellerGroup &&
          transactionGroup &&
          sellerGroup.compareDocumentPosition(transactionGroup) &
            Node.DOCUMENT_POSITION_FOLLOWING,
      ),
    ).toBe(true);
    expect(screen.getByText("必選")).toBeTruthy();

    const cash = screen.getByRole("button", { name: /現金/ });
    const storeCredit = screen.getByRole("button", { name: /購物金/ });
    const submit = screen.getByRole("button", { name: "確認並送出" });
    expect(cash.classList.contains("kiosk-payout-btn--active")).toBe(false);
    expect(storeCredit.classList.contains("kiosk-payout-btn--active")).toBe(false);
    expect((submit as HTMLButtonElement).disabled).toBe(true);

    await user.click(storeCredit);
    expect(storeCredit.classList.contains("kiosk-payout-btn--active")).toBe(true);
    expect(cash.classList.contains("kiosk-payout-btn--active")).toBe(false);
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "模擬簽名" }));
    expect((submit as HTMLButtonElement).disabled).toBe(false);
  });

  it("簽署仍為 SIGNED 時，PAYMENT_UNCERTAIN 必須蓋過完成畫面並警告勿重複付款", async () => {
    window.localStorage.setItem(
      "lu-camp.kiosk.csrf",
      "csrf-token-at-least-thirty-two-characters",
    );
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

  it("成交完成畫面顯示實際剩餘秒數", async () => {
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
            status: "COMPLETED",
            revision: 9,
            snapshot: {
              content_version: "cart-v1",
              items: [],
              total: "1000",
              discount_total: "0",
              campaign_name: null,
              member: null,
              tenders: [{ tender_type: "CASH", amount: "1000" }],
            },
            changes: [],
            updated_at: new Date().toISOString(),
          });
        }
        if (request.url.endsWith("/api/v1/kiosk/tasks/current")) return json(null);
        if (request.url.endsWith("/api/v1/kiosk/heartbeat")) {
          return json({ online: true, last_seen_at: new Date().toISOString() });
        }
        throw new Error(`unmatched fetch ${request.method} ${request.url}`);
      }),
    );

    renderPage();

    expect(await screen.findByText("交易已完成")).toBeTruthy();
    expect(screen.getByText(/謝謝光臨，10 秒後自動清除。/)).toBeTruthy();
  });

  it("升級後殘留的舊交回鎖不得讓下一張任務要求店員帳密", async () => {
    // 舊版會留下 kiosk-handoff=1 與當時的 kiosk-engaged；交回鎖移除後若不一併清掉，
    // 升級後第一張任務會被當成「任務已更新」而擋在帳密閘門——正是本次要消除的重複登入。
    window.localStorage.setItem(
      "lu-camp.kiosk.csrf",
      "csrf-token-at-least-thirty-two-characters",
    );
    window.localStorage.setItem("lu-camp.kiosk-handoff", "1");
    window.localStorage.setItem("lu-camp.kiosk-engaged", "41"); // 已結束的舊任務
    const upgradedTask = {
      id: 99,
      store_id: 1,
      kind: "STORE_CREDIT_USE",
      status: "PENDING",
      contact_id: 7,
      content: {
        total: "500",
        store_credit_amount: "200",
        store_credit_balance_before: "800",
        store_credit_balance_after: "600",
        remaining_tenders: [{ tender_type: "CASH", amount: "300" }],
        items: [{ name: "營燈", qty: 1, unit_price: "500", line_total: "500" }],
        member: { display_name: "王○○" },
        discount_total: "0",
        campaign_name: null,
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
        if (request.url.includes("/api/v1/kiosk/tasks/current")) {
          return json(upgradedTask); // 升級後推來的新任務
        }
        if (request.url.includes("/acknowledge")) {
          return json({ ...upgradedTask, status: "SIGNING" });
        }
        if (request.url.endsWith("/api/v1/kiosk/heartbeat")) {
          return json({ online: true, last_seen_at: "2026-07-24T10:01:00Z" });
        }
        return json({});
      }),
    );

    renderPage();

    // 新任務直接顯示，不出現任何帳密欄位
    expect(await screen.findByText(/收購確認與切結|購物金使用確認/)).toBeTruthy();
    expect(screen.queryByLabelText("店員帳號")).toBeNull();
    expect(window.localStorage.getItem("lu-camp.kiosk-handoff")).toBeNull();
  });

  it("成交完成畫面到期後清除舊簽署鎖並回待機", async () => {
    window.localStorage.setItem(
      "lu-camp.kiosk.csrf",
      "csrf-token-at-least-thirty-two-characters",
    );
    window.localStorage.setItem("lu-camp.kiosk-signing", "1");
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
    expect(await screen.findByText("櫃檯 · 主櫃檯")).toBeTruthy();
    await waitFor(() => {
      expect(window.localStorage.getItem("lu-camp.kiosk-signing")).toBeNull();
      expect(window.localStorage.getItem("lu-camp.kiosk-engaged")).toBeNull();
    });
    expect(screen.queryByText("已完成簽署")).toBeNull();
  });
});
