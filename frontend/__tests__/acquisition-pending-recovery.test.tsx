// @vitest-environment jsdom
// 收購頁「未確認收購」復原閘門（Codex K4 第十九輪）：重掛時若 localStorage 有殘留冪等鍵，
// 須即刻顯示復原提示並停用送出，避免以相同內容靜默重放舊收購。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import AcquisitionPage from "@/app/(authed)/acquisition/page";
import { clearPendingAcqIdemKey, savePendingAcqIdemKey } from "@/lib/idempotency";
import { clearToken, setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return render(<AcquisitionPage />, { wrapper });
}

beforeEach(() => {
  setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));
  clearPendingAcqIdemKey();
  global.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = input instanceof Request ? input.url : String(input);
    if (url.includes("/categories")) return json([]); // categories 與 pricing-rules 皆回陣列
    if (url.includes("/cash-sessions/current")) return json(null); // 無開帳
    return json({});
  }) as unknown as typeof fetch;
});

afterEach(() => {
  cleanup();
  clearPendingAcqIdemKey();
  clearToken();
  vi.restoreAllMocks();
});

describe("收購頁未確認收購復原閘門", () => {
  const submitBtn = () => screen.getByRole("button", { name: "送出收購" }) as HTMLButtonElement;

  it("無殘留鍵：不顯示復原提示、送出可用", async () => {
    renderPage();
    await screen.findByRole("button", { name: "送出收購" });
    expect(screen.queryByText(/未確認的收購/)).toBeNull();
    expect(submitBtn().disabled).toBe(false);
  });

  it("重掛時有殘留鍵：顯示復原提示且送出停用；開新單後解除", async () => {
    // 模擬前一次掛載送出後回應遺失、鍵殘留於 localStorage，然後頁面重掛（重新渲染）。
    savePendingAcqIdemKey("leftover-key");
    renderPage();

    expect(await screen.findByText(/未確認的收購/)).toBeTruthy();
    expect(submitBtn().disabled).toBe(true);

    // 店員核對確定未建立 → 開新單 → 清鍵、解除閘門、送出恢復可用。
    await userEvent.click(screen.getByRole("button", { name: "確定未建立，開新單" }));
    expect(screen.queryByText(/未確認的收購/)).toBeNull();
    expect(submitBtn().disabled).toBe(false);
  });
});
