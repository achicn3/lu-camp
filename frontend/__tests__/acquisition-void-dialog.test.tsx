// @vitest-environment jsdom
// F6.5 作廢收購確認對話框測試（原因必填、送出、錯誤對應）。作廢入口在收購紀錄清單（見 acquisition-records）。
// 本專案不使用 jest-dom matchers，沿用 vanilla 斷言（toBeTruthy / .disabled / textContent）。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

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
