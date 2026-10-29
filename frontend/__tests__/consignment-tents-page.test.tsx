// @vitest-environment jsdom
// 「官網寄售帳篷」管理頁（docs/97 §6）：列在庫寄售品與官網資料、編輯存檔後立刻推上官網、顯示同步狀態。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TentListingsPage } from "@/features/consignment-tents/TentListingsPage";
import { setToken } from "@/lib/auth";
import type { components } from "@/lib/api-types";

type Tent = components["schemas"]["TentListingRead"];
type Status = components["schemas"]["TentPublishStatusRead"];

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

const TENT: Tent = {
  item_id: 7,
  item_code: "T-2611",
  name: "Amenity Dome M",
  brand_name: "Snow Peak",
  listed_price: "6800",
  published: false,
  reserved: false,
  color: null,
  nights: null,
  missing: [],
  defects: [],
  extras: [],
  video_url: null,
  photos: [],
  updated_at: null,
};

const STATUS: Status = {
  configured: true,
  pending: false,
  pushed_at: "2026-10-30T06:20:00Z",
  last_error: null,
  last_attempt_at: "2026-10-30T06:20:00Z",
};

type Call = { method: string; url: string; body: string };

function stub(overrides: { status?: Status; tents?: Tent[] } = {}) {
  const calls: Call[] = [];
  let tents = overrides.tents ?? [TENT];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      const body =
        input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
      calls.push({ method, url, body });
      if (url.endsWith("/online-order/tents/status")) return json(overrides.status ?? STATUS);
      if (url.endsWith("/online-order/tents/publish")) return json({ ...STATUS, pending: false });
      if (method === "PUT" && url.endsWith("/online-order/tents/7")) {
        const saved = { ...TENT, ...JSON.parse(body) } as Tent;
        tents = [saved];
        return json(saved);
      }
      if (url.endsWith("/online-order/tents")) return json(tents);
      throw new Error(`unmatched fetch: ${method} ${url}`);
    }),
  );
  return calls;
}

function renderPage() {
  setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<TentListingsPage />, { wrapper: Wrapper });
}

beforeEach(() => {
  vi.useRealTimers();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("官網寄售帳篷", () => {
  it("列出在庫寄售品，標出還缺的資料", async () => {
    stub();
    renderPage();
    const row = await screen.findByRole("row", { name: /T-2611/ });
    expect(within(row).getByText("Snow Peak Amenity Dome M")).toBeTruthy();
    expect(within(row).getByText("$6,800")).toBeTruthy();
    expect(within(row).getByText("未上架")).toBeTruthy();
    expect(within(row).getByText("沒照片、沒填露數、沒填顏色")).toBeTruthy();
    expect(screen.getByText(/官網已同步/)).toBeTruthy();
  });

  it("編輯後存檔：一行一項送出，接著立刻推上官網", async () => {
    const calls = stub();
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "編輯 T-2611" }));
    const dialog = await screen.findByRole("dialog", { name: /T-2611/ });
    fireEvent.click(within(dialog).getByLabelText("上架官網"));
    fireEvent.change(within(dialog).getByLabelText("顏色"), { target: { value: "象牙白" } });
    fireEvent.change(within(dialog).getByLabelText("使用過幾露"), { target: { value: "6" } });
    fireEvent.change(within(dialog).getByLabelText(/瑕疵/), {
      target: { value: "外帳頂部一處淡色污漬\n\n  收納袋有使用痕跡 " },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "儲存" }));
    await waitFor(() =>
      expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/tents/publish"))).toBe(
        true,
      ),
    );
    const put = calls.find((c) => c.method === "PUT");
    expect(put).toBeDefined();
    expect(JSON.parse(put?.body ?? "{}")).toMatchObject({
      published: true,
      reserved: false,
      color: "象牙白",
      nights: 6,
      defects: ["外帳頂部一處淡色污漬", "收納袋有使用痕跡"],
      missing: [],
      video_url: null,
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("露數不是整數：擋下不送", async () => {
    const calls = stub();
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "編輯 T-2611" }));
    const dialog = await screen.findByRole("dialog", { name: /T-2611/ });
    fireEvent.change(within(dialog).getByLabelText("使用過幾露"), { target: { value: "2.5" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "儲存" }));
    expect(await within(dialog).findByRole("alert")).toBeTruthy();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("推送失敗時把原因顯示在最上方", async () => {
    stub({ status: { ...STATUS, pending: true, last_error: "連不上線上點餐雲端" } });
    renderPage();
    expect(await screen.findByText(/連不上線上點餐雲端/)).toBeTruthy();
  });

  it("沒設定雲端：說明官網不會更新", async () => {
    stub({ status: { ...STATUS, configured: false, pushed_at: null, last_attempt_at: null } });
    renderPage();
    expect(await screen.findByText(/尚未設定線上點餐雲端/)).toBeTruthy();
  });
});
