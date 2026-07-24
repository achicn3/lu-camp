// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  PosCustomerDisplay,
  restoreLines,
} from "@/features/customer-display/PosCustomerDisplay";
import type { components } from "@/lib/api-types";

type SaleLine = components["schemas"]["SaleLineCreateRequest"];
type Tender = components["schemas"]["CartTenderRequest"];

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }
  return Wrapper;
}

function wrapperWithClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
  }
  return { client, Wrapper };
}

const LINE: SaleLine = {
  line_type: "CATALOG",
  item_code: null,
  catalog_product_id: 6,
  bulk_lot_id: null,
  menu_item_id: null,
  qty: 2,
};
const TENDERS: Tender[] = [{ tender_type: "CASH", amount: "240" }];

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("POS 客顯同步", () => {
  it("能從伺服器快照重建四種商品識別，不使用顧客端自行計價", () => {
    expect(
      restoreLines([
        {
          item_key: "SERIALIZED:S1-ABC",
          line_type: "SERIALIZED",
          name: "帳篷",
          qty: 1,
          unit_price: "1800",
          original_unit_price: null,
          discount_amount: "0",
          line_total: "1800",
        },
        {
          item_key: "CATALOG:6",
          line_type: "CATALOG",
          name: "瓦斯罐",
          qty: 2,
          unit_price: "120",
          original_unit_price: null,
          discount_amount: "0",
          line_total: "240",
        },
      ]),
    ).toEqual([
      expect.objectContaining({
        key: "S:S1-ABC",
        itemCode: "S1-ABC",
        unitPrice: 1800,
      }),
      expect.objectContaining({
        key: "C:6",
        catalogProductId: 6,
        unitPrice: 120,
        qty: 2,
      }),
    ]);
  });

  it("註冊固定櫃檯後，以 revision 將本機購物車同步到已配對客顯", async () => {
    const putBodies: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(input);
        if (
          request.url.endsWith("/api/v1/customer-display/terminals") &&
          request.method === "POST"
        ) {
          return json({
            id: 3,
            installation_id: "10000000-0000-4000-8000-000000000003",
            name: "主要櫃檯",
            paired_kiosk: {
              id: 8,
              label: "顧客平板",
              online: true,
              last_seen_at: "2026-07-24T10:00:00Z",
              current_session_id: null,
              displayed_revision: 0,
            },
          });
        }
        if (request.url.endsWith("/terminals/3/cart/current")) return json(null);
        if (request.url.endsWith("/terminals/3/cart") && request.method === "PUT") {
          putBodies.push(await request.clone().json());
          return json({
            id: 21,
            status: "DRAFT",
            revision: 1,
            pos_terminal_id: 3,
            kiosk_device_id: 8,
            snapshot: {
              content_version: "cart-v1",
              items: [],
              total: "240",
              discount_total: "0",
              campaign_name: null,
              member: null,
              tenders: TENDERS,
            },
            changes: [],
            created_at: "2026-07-24T10:00:00Z",
            updated_at: "2026-07-24T10:00:00Z",
          });
        }
        throw new Error(`unmatched fetch ${request.method} ${request.url}`);
      }),
    );
    const onRestore = vi.fn();
    const view = render(
      <PosCustomerDisplay
        lines={[]}
        buyerContactId={null}
        tenders={[]}
        ready
        onRestore={onRestore}
      />,
      { wrapper: wrapper() },
    );
    expect(await screen.findByText(/客顯已連線/)).toBeTruthy();

    view.rerender(
      <PosCustomerDisplay
        lines={[LINE]}
        buyerContactId={null}
        tenders={TENDERS}
        ready
        onRestore={onRestore}
      />,
    );

    await waitFor(() => expect(putBodies).toHaveLength(1));
    expect(putBodies[0]).toEqual({
      expected_revision: null,
      lines: [LINE],
      buyer_contact_id: null,
      tenders: TENDERS,
    });
  });

  it("外部撤回解凍帶回較新 revision 後，下一次同步使用新的 CAS 基準", async () => {
    const putBodies: Record<string, unknown>[] = [];
    let responseRevision = 1;
    const serverCart = (revision: number) => ({
      id: 21,
      status: "DRAFT",
      revision,
      pos_terminal_id: 3,
      kiosk_device_id: 8,
      snapshot: {
        content_version: "cart-v1",
        items: [],
        total: "240",
        discount_total: "0",
        campaign_name: null,
        member: null,
        tenders: TENDERS,
      },
      changes: [],
      created_at: "2026-07-24T10:00:00Z",
      updated_at: "2026-07-24T10:00:00Z",
      buyer_contact_id: null,
      active_signature_task_id: null,
      payment_order_id: null,
      payment_uncertain_at: null,
      payment_uncertain_reason: null,
      sale_id: null,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input instanceof Request ? input : new Request(input);
        if (
          request.url.endsWith("/api/v1/customer-display/terminals") &&
          request.method === "POST"
        ) {
          return json({
            id: 3,
            installation_id: "10000000-0000-4000-8000-000000000003",
            name: "主要櫃檯",
            paired_kiosk: {
              id: 8,
              label: "顧客平板",
              online: true,
              last_seen_at: "2026-07-24T10:00:00Z",
              current_session_id: null,
              displayed_revision: 0,
            },
          });
        }
        if (request.url.endsWith("/terminals/3/cart/current")) return json(null);
        if (
          request.url.endsWith("/terminals/3/cart") &&
          request.method === "PUT"
        ) {
          putBodies.push(await request.clone().json());
          return json(serverCart(responseRevision++));
        }
        throw new Error(`unmatched fetch ${request.method} ${request.url}`);
      }),
    );
    const { client, Wrapper } = wrapperWithClient();
    const view = render(
      <PosCustomerDisplay
        lines={[]}
        buyerContactId={null}
        tenders={[]}
        ready
        onRestore={vi.fn()}
      />,
      { wrapper: Wrapper },
    );
    await screen.findByText(/客顯已連線/);
    view.rerender(
      <PosCustomerDisplay
        lines={[LINE]}
        buyerContactId={null}
        tenders={TENDERS}
        ready
        onRestore={vi.fn()}
      />,
    );
    await waitFor(() => expect(putBodies).toHaveLength(1));

    // 模擬簽署 freeze→cancel 在其他 mutation 中把 revision 從 1 推到 3。
    client.setQueryData(
      ["customer-display", "cart", 3],
      serverCart(3),
    );
    await screen.findByText("購物車版本 3");
    view.rerender(
      <PosCustomerDisplay
        lines={[LINE]}
        buyerContactId={null}
        tenders={[{ tender_type: "TAIWAN_PAY", amount: "240" }]}
        ready
        onRestore={vi.fn()}
      />,
    );
    await waitFor(() => expect(putBodies).toHaveLength(2));
    expect(putBodies[1]).toMatchObject({ expected_revision: 3 });
  });
});
