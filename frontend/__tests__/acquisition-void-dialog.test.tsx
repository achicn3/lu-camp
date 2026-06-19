// @vitest-environment jsdom
// F6.5 作廢收購元件測試：確認對話框（原因必填、送出、錯誤對應）＋查詢區（摘要與作廢入口閘）。
// 本專案不使用 jest-dom matchers，沿用 vanilla 斷言（toBeTruthy / .disabled / textContent）。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { VoidAcquisitionSection } from "@/features/acquisition/VoidAcquisitionSection";
import { VoidConfirmDialog } from "@/features/acquisition/VoidConfirmDialog";

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function wrap(ui: ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function acquisition(over: Record<string, unknown> = {}) {
  return {
    id: 5,
    store_id: 1,
    type: "BUYOUT",
    contact_id: 7,
    clerk_user_id: 2,
    total_cash_paid: "1800",
    payout_method: "CASH",
    payout_cash_amount: "1800",
    payout_credit_cash_equivalent: null,
    note: null,
    created_at: "2026-06-18T03:00:00Z",
    voided_at: null,
    ...over,
  };
}

function confirmButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: "確認作廢" }) as HTMLButtonElement;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("VoidConfirmDialog", () => {
  it("原因為空時確認鍵停用；輸入後送出帶 reason，成功回呼 onVoided", async () => {
    const result = {
      acquisition_id: 5,
      voided_at: "2026-06-19T00:00:00Z",
      reversed_cash: "1800",
      reversed_credit: "0",
    };
    const seen: { url: string; method: string; body: string }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: Request) => {
        seen.push({ url: input.url, method: input.method, body: await input.clone().text() });
        return json(result);
      }),
    );
    const onVoided = vi.fn();
    wrap(<VoidConfirmDialog acquisitionId={5} onClose={vi.fn()} onVoided={onVoided} />);

    expect(confirmButton().disabled).toBe(true);
    await userEvent.type(screen.getByLabelText("作廢原因"), "金額打錯");
    expect(confirmButton().disabled).toBe(false);
    await userEvent.click(confirmButton());

    await waitFor(() => expect(onVoided).toHaveBeenCalledWith(result));
    expect(seen[0].method).toBe("POST");
    expect(seen[0].url).toContain("/acquisitions/5/void");
    expect(JSON.parse(seen[0].body)).toEqual({ reason: "金額打錯" });
  });

  it("純空白原因不可送出（確認鍵維持停用）", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({})));
    wrap(<VoidConfirmDialog acquisitionId={5} onClose={vi.fn()} onVoided={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("作廢原因"), "   ");
    expect(confirmButton().disabled).toBe(true);
  });

  it("後端 409 → 顯示後端 detail 訊息", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ detail: "收購含已售出的庫存，不可作廢" }, 409)),
    );
    wrap(<VoidConfirmDialog acquisitionId={9} onClose={vi.fn()} onVoided={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("作廢原因"), "誤建");
    await userEvent.click(confirmButton());
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("收購含已售出的庫存");
  });
});

describe("VoidAcquisitionSection", () => {
  async function lookup(id = "5") {
    await userEvent.type(screen.getByLabelText("收購單號"), id);
    await userEvent.click(screen.getByRole("button", { name: "查詢" }));
  }

  it("查詢買斷單 → 顯示摘要與作廢入口", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(acquisition())));
    wrap(<VoidAcquisitionSection />);
    await lookup();
    expect(await screen.findByText("買斷")).toBeTruthy();
    expect(screen.getByRole("button", { name: "作廢收購" })).toBeTruthy();
  });

  it("寄售單 → 顯示不支援作廢，無作廢入口", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(acquisition({ type: "CONSIGNMENT" }))));
    wrap(<VoidAcquisitionSection />);
    await lookup();
    expect(await screen.findByText(/不支援作廢/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "作廢收購" })).toBeNull();
  });

  it("已作廢單 → 顯示已作廢，無作廢入口", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(acquisition({ voided_at: "2026-06-18T05:00:00Z" }))));
    wrap(<VoidAcquisitionSection />);
    await lookup();
    expect(await screen.findByText(/不可重複作廢/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "作廢收購" })).toBeNull();
  });

  it("查無收購單 → 顯示錯誤訊息", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ detail: "找不到收購單" }, 404)));
    wrap(<VoidAcquisitionSection />);
    await lookup("999");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("找不到收購單");
  });
});
