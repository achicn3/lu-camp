// @vitest-environment jsdom
// 收購紀錄「改撥款方式」（店主 2026-10-10）：客人反悔，購物金 ↔ 現金，整合成一顆按鈕。
// 全額單一撥款、沒作廢的單才有按鈕；確認視窗講清楚方向與金額；成功後告訴店員要付／收多少。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChangePayoutAction, payoutChangeTarget } from "@/features/acquisition/ChangePayoutAction";
import type { components } from "@/lib/api-types";

type Row = components["schemas"]["AcquisitionListItem"];

const CREDIT_ROW: Row = {
  id: 282,
  created_at: "2026-10-10T08:43:37Z",
  type: "BUYOUT",
  contact_id: 17,
  seller_name: "王小明",
  clerk_name: "dev",
  item_count: 16,
  item_names: ["冰桶"],
  payout_method: "STORE_CREDIT",
  total_cash_paid: "0",
  payout_cash_amount: "0",
  payout_credit_cash_equivalent: "8470",
  voided_at: null,
  void_block: null,
  pending_listing_count: 16,
  payout_change_to: "CASH",
};

const CASH_ROW: Row = {
  ...CREDIT_ROW,
  id: 300,
  payout_method: "CASH",
  total_cash_paid: "8470",
  payout_cash_amount: "8470",
  payout_credit_cash_equivalent: "0",
  payout_change_to: "STORE_CREDIT",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Call = { url: string; method: string; body: unknown };

function stub(route: (url: string, method: string) => Response): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const request = input as Request;
      const body = request.method === "GET" ? null : await request.clone().json().catch(() => null);
      calls.push({ url: request.url, method: request.method, body });
      return route(request.url, request.method);
    }),
  );
  return calls;
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("改撥款方式", () => {
  it("照後端給的 payout_change_to 決定能改成什麼；不能改就不顯示按鈕", () => {
    expect(payoutChangeTarget(CREDIT_ROW)).toBe("CASH");
    expect(payoutChangeTarget(CASH_ROW)).toBe("STORE_CREDIT");
    expect(payoutChangeTarget({ ...CASH_ROW, payout_change_to: null })).toBeNull();
    render(
      <ChangePayoutAction
        row={{ ...CASH_ROW, payout_change_to: null }}
        onDone={() => {}}
        onError={() => {}}
      />,
      { wrapper },
    );
    expect(screen.queryByRole("button", { name: "改撥款方式" })).toBeNull();
  });

  it("購物金 → 現金：視窗講付現金額；送出後提示付現與扣回", async () => {
    const calls = stub(() =>
      json({ acquisition_id: 282, payout_method: "CASH", cash: "8470", store_credit: "8894" }),
    );
    const onDone = vi.fn();
    const user = userEvent.setup();
    render(<ChangePayoutAction row={CREDIT_ROW} onDone={onDone} onError={() => {}} />, { wrapper });

    await user.click(screen.getByRole("button", { name: "改撥款方式" }));
    const dialog = screen.getByRole("dialog", { name: "改撥款方式" });
    expect(dialog.textContent).toContain("購物金 → 現金");
    expect(dialog.textContent).toContain("$8,470");
    expect(dialog.textContent).toContain("不用重新簽名");
    await user.click(screen.getByRole("button", { name: "確定改成現金" }));

    await waitFor(() => expect(onDone).toHaveBeenCalled());
    const post = calls.find((c) => c.method === "POST");
    expect(post?.url).toContain("/api/v1/acquisitions/282/change-payout");
    expect(post?.body).toEqual({ payout_method: "CASH" });
    expect(onDone).toHaveBeenCalledWith(
      "收購單 #282 已改成現金：請從抽屜拿現金 $8,470 給客人；客人的購物金已扣回 $8,894。",
    );
  });

  it("現金 → 購物金：視窗用目前溢價率試算購物金；送出後提示收回現金與撥出購物金", async () => {
    const calls = stub((url, method) =>
      method === "GET" && url.includes("/settings")
        ? json({ premium_rate: "0.0500" })
        : json({
            acquisition_id: 300,
            payout_method: "STORE_CREDIT",
            cash: "8470",
            store_credit: "8894",
          }),
    );
    const onDone = vi.fn();
    const user = userEvent.setup();
    render(<ChangePayoutAction row={CASH_ROW} onDone={onDone} onError={() => {}} />, { wrapper });

    await user.click(screen.getByRole("button", { name: "改撥款方式" }));
    const dialog = screen.getByRole("dialog", { name: "改撥款方式" });
    expect(dialog.textContent).toContain("現金 → 購物金");
    await waitFor(() => expect(dialog.textContent).toContain("$8,894"));
    expect(dialog.textContent).toContain("$8,470");
    await user.click(screen.getByRole("button", { name: "確定改成購物金" }));

    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ payout_method: "STORE_CREDIT" });
    expect(onDone).toHaveBeenCalledWith(
      "收購單 #300 已改成購物金：請向客人收回現金 $8,470 放進抽屜；已撥給客人購物金 $8,894。",
    );
  });

  it("後端擋下（例如購物金已花掉）：關掉視窗、把原因交給清單顯示", async () => {
    stub(() => json({ detail: "客人的購物金已經花掉一部分，不能改成付現" }, 409));
    const onError = vi.fn();
    const user = userEvent.setup();
    render(<ChangePayoutAction row={CREDIT_ROW} onDone={() => {}} onError={onError} />, {
      wrapper,
    });

    await user.click(screen.getByRole("button", { name: "改撥款方式" }));
    await user.click(screen.getByRole("button", { name: "確定改成現金" }));

    await waitFor(() =>
      expect(onError).toHaveBeenCalledWith("客人的購物金已經花掉一部分，不能改成付現"),
    );
    expect(screen.queryByRole("dialog", { name: "改撥款方式" })).toBeNull();
  });
});
