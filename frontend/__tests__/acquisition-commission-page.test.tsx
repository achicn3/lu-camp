// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import AcquisitionPage from "@/app/(authed)/acquisition/page";
import { setToken } from "@/lib/token";

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("/acquisition 寄售抽成", () => {
  it("新寄售列採店內預設，仍可逐件修改", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("/settings")) {
          return json({
            store_id: 1,
            einvoice_enabled: false,
            tax_rate: "0.05",
            default_commission_pct: 37,
            default_margin_pct: 45,
            premium_rate: "0.10",
          });
        }
        if (url.includes("/categories")) return json([]);
        if (url.includes("/cash-sessions/current")) return json(null);
        throw new Error(`unmatched fetch: ${url}`);
      }),
    );
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("tab", { name: "寄售" }));
    await waitFor(() => {
      expect((screen.getByLabelText("抽成 %（寄售）") as HTMLInputElement).value).toBe("37");
    });
    await user.click(screen.getByRole("button", { name: "＋ 新增一列" }));
    const commissions = screen.getAllByLabelText("抽成 %（寄售）");
    expect(commissions).toHaveLength(2);
    expect((commissions[1] as HTMLInputElement).value).toBe("37");

    await user.clear(commissions[1]);
    await user.type(commissions[1], "42");
    expect((commissions[0] as HTMLInputElement).value).toBe("37");
    expect((commissions[1] as HTMLInputElement).value).toBe("42");
  });

  it("未手改抽成時只顯示預設，不把開頁時的舊值當逐件覆寫送出", async () => {
    const consignor = {
      id: 7,
      store_id: 1,
      name: "王寄售人",
      phone: "0912345678",
      roles: ["CONSIGNOR"],
      national_id_masked: "A12****789",
      has_national_id: true,
    };
    const submitted: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const request = input instanceof Request ? input : null;
        const url = request?.url ?? String(input);
        const method = request?.method ?? init?.method ?? "GET";
        if (url.includes("/settings")) {
          return json({
            store_id: 1,
            einvoice_enabled: false,
            tax_rate: "0.05",
            default_commission_pct: 37,
            default_margin_pct: 45,
            premium_rate: "0.10",
          });
        }
        if (url.includes("/categories/") && url.includes("/pricing-rules")) return json([]);
        if (url.includes("/categories")) {
          return json([{ id: 1, name: "相機", target_margin_pct: 45 }]);
        }
        if (url.includes("/cash-sessions/current")) return json(null);
        if (url.includes("/contacts") && method === "GET") return json([consignor]);
        if (url.includes("/item-name-suggestions")) return json([]);
        if (url.includes("/acquisitions") && method === "POST") {
          const raw = request ? await request.clone().text() : String(init?.body ?? "");
          submitted.push(JSON.parse(raw) as Record<string, unknown>);
          return new Response(
            JSON.stringify({
              acquisition_id: 99,
              type: "CONSIGNMENT",
              contact_id: 7,
              total_cash_paid: null,
              payout_method: "CASH",
              payout_cash_amount: null,
              payout_credit_cash_equivalent: null,
              payout_credit_granted: null,
              payout_credit_balance_after: null,
              item_codes: ["C-99"],
              lot_code: null,
            }),
            { status: 201, headers: { "Content-Type": "application/json" } },
          );
        }
        throw new Error(`unmatched fetch: ${method} ${url}`);
      }),
    );
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByRole("tab", { name: "寄售" }));
    await user.type(screen.getByLabelText("賣方搜尋"), "王");
    await user.click(await screen.findByRole("button", { name: /王寄售人/ }));
    await user.type(screen.getByLabelText("品名"), "底片相機");
    await user.selectOptions(screen.getByLabelText("成色"), "A");
    await user.click(screen.getByLabelText("分類"));
    await user.click(await screen.findByRole("option", { name: "相機" }));
    await user.type(
      screen.getByLabelText("上架售價（含稅）", { selector: "input" }),
      "2000",
    );
    expect((screen.getByLabelText("抽成 %（寄售）") as HTMLInputElement).value).toBe("37");

    await user.click(screen.getByRole("button", { name: "送出收購" }));
    await waitFor(() => expect(submitted).toHaveLength(1));
    const items = submitted[0].items as Array<Record<string, unknown>>;
    expect(items).toHaveLength(1);
    expect(items[0]).not.toHaveProperty("commission_pct");
  });
});
