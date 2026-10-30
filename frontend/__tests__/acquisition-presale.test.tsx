// @vitest-environment jsdom
// 收購頁寄售「從預售帶入」（docs/102）：選了預售就帶入品名、品牌、售價，送出時帶 presale_listing_id；
// 買斷不出現、沒選就不送（維持舊冪等指紋）。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import AcquisitionPage from "@/app/(authed)/acquisition/page";
import type { components } from "@/lib/api-types";
import { setToken } from "@/lib/token";

type Presale = components["schemas"]["TentPresaleRead"];

const PRESALE: Presale = {
  listing_id: 41,
  presale_code: "P1-0A1B2C3D4E",
  brand_id: 3,
  brand_name: "KZM",
  name: "NEW X5",
  price: "14999",
  seller_name: "王小明",
  seller_phone: "0912345678",
  note: null,
  published: true,
  reserved: false,
  color: "灰",
  nights: 4,
  missing: [],
  defects: [],
  extras: [],
  video_url: null,
  photos: [],
  updated_at: "2026-10-31T02:00:00Z",
};

const CONSIGNOR = {
  id: 7,
  store_id: 1,
  name: "王小明",
  phone: "0912345678",
  roles: ["SELLER"],
  national_id_masked: "A12****789",
  has_national_id: true,
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderPage() {
  const tokenPart = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  setToken(`${tokenPart({ alg: "HS256" })}.${tokenPart({ sub: "1", role: "CLERK", store_id: 1 })}.sig`);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<AcquisitionPage />, { wrapper: Wrapper });
}

function stub() {
  const submitted: Record<string, unknown>[] = [];
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : null;
      const url = request?.url ?? String(input);
      const method = request?.method ?? init?.method ?? "GET";
      calls.push(`${method} ${url}`);
      if (url.includes("/settings")) {
        return json({
          store_id: 1,
          einvoice_enabled: false,
          tax_rate: "0.05",
          default_commission_pct: 50,
          default_margin_pct: 45,
          premium_rate: "0.10",
        });
      }
      if (url.includes("/online-order/tents/presales")) return json([PRESALE]);
      if (url.includes("/online-order/tents/publish")) return json({});
      if (url.includes("/online-order/tents")) return json([]);
      if (url.includes("/categories/") && url.includes("/pricing-rules")) return json([]);
      if (url.includes("/categories")) return json([{ id: 1, name: "帳篷", target_margin_pct: 45 }]);
      if (url.includes("/cash-sessions/current")) return json(null);
      if (url.includes("/contacts") && method === "GET") return json([CONSIGNOR]);
      if (url.includes("/item-name-suggestions")) return json([]);
      if (url.includes("/price-hint") || url.includes("/price-hints")) return json(null, 404);
      if (url.includes("/acquisitions") && method === "POST") {
        const raw = request ? await request.clone().text() : String(init?.body ?? "");
        submitted.push(JSON.parse(raw) as Record<string, unknown>);
        return json(
          {
            acquisition_id: 99,
            type: "CONSIGNMENT",
            contact_id: 7,
            total_cash_paid: null,
            payout_method: "CASH",
            payout_cash_amount: null,
            payout_credit_cash_equivalent: null,
            payout_credit_granted: null,
            payout_credit_balance_after: null,
            item_codes: ["S1-0000000099"],
            lot_code: null,
          },
          201,
        );
      }
      return json([]);
    }),
  );
  return { submitted, calls };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("/acquisition 從預售帶入", () => {
  it("選了預售：帶入品名、品牌、售價，送出時帶 presale_listing_id 並推上官網", async () => {
    const { submitted, calls } = stub();
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("tab", { name: "寄售" }));
    await user.type(screen.getByLabelText("賣方搜尋"), "王");
    await user.click(await screen.findByRole("button", { name: /王小明/ }));
    const picker = await screen.findByLabelText(/從預售帶入/);
    await user.selectOptions(picker, "41");

    expect((screen.getByLabelText("品名") as HTMLInputElement).value).toBe("NEW X5");
    expect(
      (screen.getByLabelText("上架售價（含稅與手續費）", { selector: "input" }) as HTMLInputElement)
        .value,
    ).toBe("14999");
    expect(screen.getByText("KZM")).toBeTruthy();

    await user.click(screen.getByRole("radio", { name: "A 近全新/精品" }));
    await user.click(screen.getByLabelText("分類"));
    await user.click(await screen.findByRole("option", { name: "帳篷" }));
    await user.click(screen.getByRole("button", { name: "送出收購" }));

    await waitFor(() => expect(submitted).toHaveLength(1));
    const [item] = submitted[0].items as Array<Record<string, unknown>>;
    expect(item).toMatchObject({
      presale_listing_id: 41,
      name: "NEW X5",
      brand_id: 3,
      listed_price: "14999",
    });
    await waitFor(() =>
      expect(calls.some((c) => c.startsWith("POST") && c.endsWith("/tents/publish"))).toBe(true),
    );
  });

  it("買斷不出現預售帶入", async () => {
    stub();
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("tab", { name: "寄售" }));
    expect(await screen.findByLabelText(/從預售帶入/)).toBeTruthy();
    await user.click(screen.getByRole("tab", { name: "買斷" }));
    expect(screen.queryByLabelText(/從預售帶入/)).toBeNull();
  });
});
