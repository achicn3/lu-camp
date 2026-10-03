// @vitest-environment jsdom
// 活動頁「袋裝條碼」面板（ADR-028）：填袋裡件數建立、列出、印標籤（條碼＋名稱＋組合價）。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BundlePacksPanel } from "@/features/campaigns/BundlePacksPanel";
import type { components } from "@/lib/api-types";
import { setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const CAMPAIGN = {
  id: 9,
  store_id: 1,
  name: "濾掛 12 入",
  kind: "BUNDLE",
  discount_pct: null,
  bundle_price: "500",
  bundle_slots: [
    {
      slot_no: 0,
      qty: 12,
      targets: [
        { target_type: "CATALOG_PRODUCT", target_id: 7, label: "天堂鳥濾掛" },
        { target_type: "CATALOG_PRODUCT", target_id: 8, label: "蜜桃濾掛" },
      ],
    },
  ],
  status: "ACTIVE",
} as unknown as components["schemas"]["CampaignRead"];

const PACK = {
  id: 3,
  store_id: 1,
  campaign_id: 9,
  code: "P1-ABCDEF0123",
  name: "濾掛 12 入",
  is_active: true,
  created_at: "2026-10-04T02:00:00Z",
  items: [
    { item_type: "CATALOG", target_id: 7, qty: 6, label: "天堂鳥濾掛" },
    { item_type: "CATALOG", target_id: 8, qty: 6, label: "蜜桃濾掛" },
  ],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("袋裝條碼面板", () => {
  it("填件數建立 → 列出 → 印標籤送條碼、名稱與組合價", async () => {
    setToken(fakeJwt({ sub: "1", role: "MANAGER", store_id: 1 }));
    let packs: unknown[] = [];
    const posted: unknown[] = [];
    const prints: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
        const text = input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
        if (url.endsWith("/campaigns/9/packs") && method === "GET") return json(packs);
        if (url.endsWith("/campaigns/9/packs") && method === "POST") {
          posted.push(JSON.parse(text));
          packs = [PACK];
          return json(PACK, 201);
        }
        if (url.includes("/print/label")) {
          prints.push(JSON.parse(text));
          return json({ status: "ok" });
        }
        throw new Error(`unmatched ${method} ${url}`);
      }),
    );
    const user = userEvent.setup();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <BundlePacksPanel campaign={CAMPAIGN} onClose={() => {}} />
      </QueryClientProvider>,
    );

    const form = screen.getByRole("form", { name: "建立袋裝條碼" });
    expect(within(form).getByText(/一組共 12 件/)).toBeTruthy();
    await user.type(within(form).getByLabelText("天堂鳥濾掛 件數"), "6");
    await user.type(within(form).getByLabelText("蜜桃濾掛 件數"), "6");
    expect(within(form).getByText(/已放 12／12 件/)).toBeTruthy();
    await user.click(within(form).getByRole("button", { name: "建立袋裝條碼" }));

    await waitFor(() =>
      expect(posted).toEqual([
        {
          name: "濾掛 12 入",
          items: [
            { item_type: "CATALOG", target_id: 7, qty: 6 },
            { item_type: "CATALOG", target_id: 8, qty: 6 },
          ],
        },
      ]),
    );
    await waitFor(() => expect(screen.getByText("P1-ABCDEF0123")).toBeTruthy());
    expect(screen.getByText("天堂鳥濾掛×6、蜜桃濾掛×6")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "印標籤" }));
    await waitFor(() =>
      expect(prints).toEqual([
        { code: "P1-ABCDEF0123", name: "濾掛 12 入", price: 500, brand: null, condition: null },
      ]),
    );
  });

  it("後端說湊不成一組：照原因顯示", async () => {
    setToken(fakeJwt({ sub: "1", role: "MANAGER", store_id: 1 }));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
        if (method === "GET") return json([]);
        return json({ detail: "袋裡的商品湊不成這個組合價（少放了、放錯，或組合價沒有比原價便宜）" }, 422);
      }),
    );
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <BundlePacksPanel campaign={CAMPAIGN} onClose={() => {}} />
      </QueryClientProvider>,
    );
    await user.type(screen.getByLabelText("天堂鳥濾掛 件數"), "11");
    await user.click(screen.getByRole("button", { name: "建立袋裝條碼" }));
    expect(await screen.findByText(/湊不成這個組合價/)).toBeTruthy();
  });
});
