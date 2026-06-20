// @vitest-environment jsdom
// /stocktake 盤點：建單、清單、逐項輸入實點數＋即時差異、確認調整。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import StocktakePage from "@/app/(authed)/stocktake/page";
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

const CATALOG = [
  { id: 42, store_id: 1, sku: "GAS-001", name: "瓦斯罐", brand_id: null, unit_price: "120", quantity_on_hand: 10, reorder_point: 3 },
  { id: 43, store_id: 1, sku: "ROPE-1", name: "營繩", brand_id: null, unit_price: "50", quantity_on_hand: 5, reorder_point: 2 },
];

const DRAFT_STOCKTAKE = {
  id: 3,
  store_id: 1,
  status: "DRAFT",
  created_by: 1,
  created_at: "2026-06-20T03:00:00Z",
  confirmed_by: null,
  confirmed_at: null,
  lines: [
    { id: 100, catalog_product_id: 42, system_qty: 10, counted_qty: null, variance: null },
    { id: 101, catalog_product_id: 43, system_qty: 5, counted_qty: null, variance: null },
  ],
};

type FetchRoute = (url: string, init: RequestInit) => Response | null;

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

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<StocktakePage />, { wrapper });
}

afterEach(() => {
  cleanup();
  clearToken();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("/stocktake", () => {
  it("lists stocktakes", async () => {
    loginAs("CLERK");
    stubFetch((url) => {
      if (url.includes("/catalog-products")) return json(CATALOG);
      if (url.includes("/stocktakes")) return json([DRAFT_STOCKTAKE]);
      return null;
    });
    renderPage();
    expect(await screen.findByText("#3")).toBeTruthy();
    expect(screen.getByText("盤點中")).toBeTruthy();
  });

  it("creates a stocktake and opens its detail with system quantities", async () => {
    loginAs("CLERK");
    let created = false;
    stubFetch((url, init) => {
      if (url.includes("/catalog-products")) return json(CATALOG);
      if (url.includes("/stocktakes/3")) return json(DRAFT_STOCKTAKE);
      if (url.includes("/stocktakes") && init.method === "POST") {
        created = true;
        return json(DRAFT_STOCKTAKE, 201);
      }
      if (url.includes("/stocktakes")) return json(created ? [DRAFT_STOCKTAKE] : []);
      return null;
    });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "建立盤點單" }));
    expect(await screen.findByText("盤點單 #3")).toBeTruthy();
    expect(screen.getByLabelText("實點數 瓦斯罐（GAS-001）")).toBeTruthy();
  });

  it("shows live variance and confirms with only entered counts", async () => {
    loginAs("CLERK");
    let confirmBody: string | null = null;
    const confirmed = {
      ...DRAFT_STOCKTAKE,
      status: "CONFIRMED",
      confirmed_at: "2026-06-20T04:00:00Z",
      lines: [
        { id: 100, catalog_product_id: 42, system_qty: 10, counted_qty: 7, variance: -3 },
        { id: 101, catalog_product_id: 43, system_qty: 5, counted_qty: null, variance: null },
      ],
    };
    stubFetch((url, init) => {
      if (url.includes("/catalog-products")) return json(CATALOG);
      if (url.includes("/stocktakes/3/confirm") && init.method === "POST") {
        confirmBody = init.body as string;
        return json(confirmed);
      }
      if (url.includes("/stocktakes/3")) return json(confirmBody ? confirmed : DRAFT_STOCKTAKE);
      if (url.includes("/stocktakes")) return json([DRAFT_STOCKTAKE]);
      return null;
    });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "盤點" }));
    const input = await screen.findByLabelText("實點數 瓦斯罐（GAS-001）");
    await user.type(input, "7");
    expect(await screen.findByText("-3", { selector: "span.st-var" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "確認盤點調整" }));
    await user.click(await screen.findByRole("button", { name: "確認調整" }));

    await waitFor(() => expect(confirmBody).not.toBeNull());
    const parsed = JSON.parse(confirmBody as unknown as string);
    expect(parsed.counts).toEqual([{ catalog_product_id: 42, counted_qty: 7 }]);
    expect(await screen.findByText("已確認")).toBeTruthy();
  });
});
