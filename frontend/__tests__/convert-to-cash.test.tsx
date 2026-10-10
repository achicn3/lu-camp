// @vitest-environment jsdom
// 收購紀錄「改成付現」（店主 2026-10-10）：客人選了購物金、送出後反悔要現金。
// 只有全額購物金撥款、沒作廢的單才有按鈕；確認視窗講清楚付多少現金；成功後告訴店員要付多少、扣回多少。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { canConvertToCash, ConvertToCashAction } from "@/features/acquisition/ConvertToCashAction";
import type { components } from "@/lib/api-types";

type Row = components["schemas"]["AcquisitionListItem"];

const ROW: Row = {
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
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("改成付現", () => {
  it("只有全額購物金撥款、沒作廢的收購單可以改", () => {
    expect(canConvertToCash(ROW)).toBe(true);
    expect(canConvertToCash({ ...ROW, payout_method: "CASH" })).toBe(false);
    expect(canConvertToCash({ ...ROW, payout_cash_amount: "1000" })).toBe(false);
    expect(canConvertToCash({ ...ROW, voided_at: "2026-10-10T09:00:00Z" })).toBe(false);
    expect(canConvertToCash({ ...ROW, type: "CONSIGNMENT" })).toBe(false);
  });

  it("確認視窗講清楚付多少現金；送出後告訴店員付現與扣回的購物金", async () => {
    let posted = "";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        posted = (input as Request).url;
        return json({ acquisition_id: 282, reversed_credit: "8894", cash_paid: "8470" });
      }),
    );
    const onDone = vi.fn();
    const user = userEvent.setup();
    render(<ConvertToCashAction row={ROW} onDone={onDone} onError={() => {}} />, { wrapper });

    await user.click(screen.getByRole("button", { name: "改成付現" }));
    const dialog = screen.getByRole("dialog", { name: "改成付現" });
    expect(dialog.textContent).toContain("$8,470");
    expect(dialog.textContent).toContain("不用重新簽名");
    await user.click(screen.getByRole("button", { name: "確定改成付現" }));

    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(posted).toContain("/api/v1/acquisitions/282/convert-payout-to-cash");
    expect(onDone).toHaveBeenCalledWith(
      "收購單 #282 已改成付現：請從抽屜拿現金 $8,470 給客人；客人的購物金已扣回 $8,894。",
    );
  });

  it("後端擋下（例如購物金已花掉）：關掉視窗、把原因交給清單顯示", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ detail: "客人的購物金已經花掉一部分，不能改成付現" }, 409)),
    );
    const onError = vi.fn();
    const user = userEvent.setup();
    render(<ConvertToCashAction row={ROW} onDone={() => {}} onError={onError} />, { wrapper });

    await user.click(screen.getByRole("button", { name: "改成付現" }));
    await user.click(screen.getByRole("button", { name: "確定改成付現" }));

    await waitFor(() =>
      expect(onError).toHaveBeenCalledWith("客人的購物金已經花掉一部分，不能改成付現"),
    );
    expect(screen.queryByRole("dialog", { name: "改成付現" })).toBeNull();
  });
});
