// @vitest-environment jsdom
// 收購頁操作速度改善（2026-09-23 裁示）：
// ② 送出後自動印標籤（設定可關）③ 繼續收同一位賣方 ④ 填好的列收合＋底部固定摘要
// ⑤ 少用的補印憑證聯／作廢收購收進「更多操作」。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import AcquisitionPage from "@/app/(authed)/acquisition/page";
import { setToken } from "@/lib/token";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const CONSIGNOR = {
  id: 7,
  store_id: 1,
  name: "王寄售人",
  phone: "0912345678",
  roles: ["CONSIGNOR"],
  national_id_masked: "A12****789",
  has_national_id: true,
};

let requests: { url: string; method: string }[] = [];

function stub({ autoPrint = true }: { autoPrint?: boolean } = {}) {
  requests = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : null;
      const url = request?.url ?? String(input);
      const method = request?.method ?? init?.method ?? "GET";
      requests.push({ url, method });
      if (url.includes("/settings")) {
        return json({
          store_id: 1,
          einvoice_enabled: false,
          tax_rate: "0.05",
          default_commission_pct: 37,
          default_margin_pct: 45,
          premium_rate: "0.10",
          auto_print_acquisition_labels: autoPrint,
        });
      }
      if (url.includes("/categories/") && url.includes("/pricing-rules")) return json([]);
      if (url.includes("/categories")) return json([{ id: 1, name: "相機", target_margin_pct: 45 }]);
      if (url.includes("/cash-sessions/current")) return json(null);
      if (url.includes("/contacts") && method === "GET") return json([CONSIGNOR]);
      if (url.includes("/item-name-suggestions")) return json([]);
      if (url.includes("/filter-options")) return json({ brands: [], categories: [], grades: [] });
      if (url.includes("/serialized-items/by-code/C-99")) {
        return json({
          item_code: "C-99",
          name: "底片相機",
          listed_price: "2000",
          brand_id: null,
          grade: "A",
        });
      }
      if (url.includes("/print/label")) return json({ ok: true });
      if (url.includes("/acquisitions") && method === "POST") {
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
            item_codes: ["C-99"],
            lot_code: null,
          },
          201,
        );
      }
      return json([]);
    }),
  );
}

function renderPage(role: "CLERK" | "MANAGER" = "CLERK") {
  const part = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");
  setToken(`${part({ alg: "HS256" })}.${part({ sub: "1", role, store_id: 1 })}.sig`);
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

/** 畫面上看得到的品名欄（收合的列仍掛著，但包在 hidden 裡）。 */
function visibleNameInputs(): HTMLElement[] {
  return screen.getAllByLabelText("品名").filter((el) => el.closest("[hidden]") === null);
}

async function submitOneConsignment(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("tab", { name: "寄售" }));
  await user.type(screen.getByLabelText("賣方搜尋"), "王");
  await user.click(await screen.findByRole("button", { name: /王寄售人/ }));
  await user.type(screen.getByLabelText("品名"), "底片相機");
  await user.selectOptions(screen.getByLabelText("成色"), "A");
  await user.click(screen.getByLabelText("分類"));
  await user.click(await screen.findByRole("option", { name: "相機" }));
  await user.type(
    screen.getByLabelText("上架售價（含稅與手續費）", { selector: "input" }),
    "2000",
  );
  await user.click(screen.getByRole("button", { name: "送出收購" }));
  await screen.findByText(/收購完成/);
}

describe("收購頁操作速度改善", () => {
  it("送出後自動印標籤，不必再按一次", async () => {
    stub({ autoPrint: true });
    const user = userEvent.setup();
    renderPage();
    await submitOneConsignment(user);
    await waitFor(() =>
      expect(requests.some((r) => r.url.includes("/print/label"))).toBe(true),
    );
    expect(await screen.findByText(/已自動送出列印/)).toBeTruthy();
  });

  it("設定關掉自動列印時不自動印，仍可手動按", async () => {
    stub({ autoPrint: false });
    const user = userEvent.setup();
    renderPage();
    await submitOneConsignment(user);
    expect(screen.getByRole("button", { name: /列印標籤/ })).toBeTruthy();
    expect(requests.some((r) => r.url.includes("/print/label"))).toBe(false);
  });

  it("送出後可以繼續收同一位賣方，不必重新搜尋", async () => {
    stub();
    const user = userEvent.setup();
    renderPage();
    await submitOneConsignment(user);
    await user.click(screen.getByRole("button", { name: /繼續收這位賣方/ }));
    expect(screen.getByText("王寄售人")).toBeTruthy();
    expect(screen.queryByText(/收購完成/)).toBeNull();
  });

  it("新增一列時，已填品名的列收合成一行摘要，點開可再改", async () => {
    stub();
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("tab", { name: "寄售" }));
    await user.type(screen.getByLabelText("品名"), "底片相機");
    await user.click(screen.getByRole("button", { name: "＋ 新增一列" }));
    const summary = screen.getByRole("button", { name: /編輯第 1 列/ });
    expect(summary.textContent).toContain("底片相機");
    // 只剩新的那一列是展開的（收合的列仍掛著、只是隱藏，選過的值才不會不見）。
    expect(visibleNameInputs()).toHaveLength(1);
    await user.click(summary);
    expect(visibleNameInputs()).toHaveLength(2);
  });

  it("收合再展開，下拉選過的分類仍在（Codex 審查）", async () => {
    stub();
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("tab", { name: "寄售" }));
    await user.type(screen.getByLabelText("品名"), "底片相機");
    await user.click(screen.getByLabelText("分類"));
    await user.click(await screen.findByRole("option", { name: "相機" }));
    await user.click(screen.getByRole("button", { name: "＋ 新增一列" }));
    await user.click(screen.getByRole("button", { name: /編輯第 1 列/ }));
    const firstRow = document.querySelectorAll(".acq-rows .acq-row")[0] as HTMLElement;
    expect(within(firstRow).getByText("相機")).toBeTruthy();
  });

  it("只有件數不對的列也會在送出時自動展開（Codex 審查）", async () => {
    stub();
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByLabelText("品名"), "焚火台");
    const qty = screen.getByLabelText("件數");
    await user.clear(qty);
    await user.type(qty, "0");
    await user.click(screen.getByRole("button", { name: "＋ 新增一列" }));
    expect(screen.getByRole("button", { name: /編輯第 1 列/ })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "送出收購" }));
    expect((await screen.findAllByText(/第 1 列：件數需為/)).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /編輯第 1 列/ })).toBeNull();
  });

  it("底部固定摘要列顯示件數與送出鈕", async () => {
    stub();
    renderPage();
    const bar = await screen.findByRole("region", { name: "收購摘要" });
    expect(within(bar).getByText(/共 1 件/)).toBeTruthy();
    expect(within(bar).getByRole("button", { name: "送出收購" })).toBeTruthy();
  });

  it("補印憑證聯與作廢收購收進「更多操作」，不佔主要動線", async () => {
    stub();
    renderPage("MANAGER");
    const reprint = await screen.findByRole("heading", { name: "補印收購憑證聯" });
    const more = reprint.closest("details");
    expect(more).not.toBeNull();
    expect(within(more as HTMLElement).getByText(/更多操作/)).toBeTruthy();
    expect(within(more as HTMLElement).getByRole("heading", { name: /作廢收購/ })).toBeTruthy();
  });

  it("送出時某一列沒填完：自動展開那一列，不讓錯誤藏在收合的摘要裡", async () => {
    stub();
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("tab", { name: "寄售" }));
    await user.type(screen.getByLabelText("品名"), "底片相機");
    await user.click(screen.getByRole("button", { name: "＋ 新增一列" }));
    expect(screen.getByRole("button", { name: /編輯第 1 列/ })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "送出收購" }));
    expect(await screen.findByText(/第 1 列：分類必選/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /編輯第 1 列/ })).toBeNull();
    expect(visibleNameInputs()).toHaveLength(2);
  });

  it("送出成功後自動捲到完成卡片（結果、標籤、繼續收都在那裡）", async () => {
    stub();
    const scrolled: Element[] = [];
    Element.prototype.scrollIntoView = vi.fn(function (this: Element) {
      scrolled.push(this);
    });
    const user = userEvent.setup();
    renderPage();
    await submitOneConsignment(user);
    await waitFor(() =>
      expect(scrolled.some((el) => el.classList.contains("acq-result"))).toBe(true),
    );
  });
});
