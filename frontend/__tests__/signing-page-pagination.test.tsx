// @vitest-environment jsdom
// 簽署紀錄的分頁：換頁時總筆數要一起重抓。
// 停在舊總數的話，別台新增的那幾筆會讓最後一頁算少一頁——「下一頁」按不下去，
// 那些資料就再也看不到（Codex 審查指出）。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import SigningPage from "@/app/(authed)/signing/page";
import { clearToken, setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function task(id: number) {
  return {
    id,
    kind: "STORE_CREDIT_USE",
    status: "SIGNED",
    contact_id: 1,
    contact_name: `客人${id}`,
    created_at: "2026-09-12T02:00:00Z",
    signed_at: "2026-09-12T02:01:00Z",
    ref_type: null,
    ref_id: null,
    content: {},
    agreement_version: null,
    agreement_title: null,
    chosen_payout: null,
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<SigningPage />, { wrapper });
}

afterEach(() => {
  cleanup();
  clearToken();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("/signing 分頁", () => {
  it("換頁時重新取得總筆數，別台剛新增的資料不會被關在最後一頁外面", async () => {
    setToken(fakeJwt({ sub: "1", role: "MANAGER", store_id: 1 }));
    let total = 40; // 剛好兩頁
    const countCalls: number[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        const json = (data: unknown) =>
          new Response(JSON.stringify(data), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        if (url.pathname.endsWith("/tasks/count")) {
          countCalls.push(total);
          return json({ count: total });
        }
        const limit = Number(url.searchParams.get("limit") ?? "20");
        const offset = Number(url.searchParams.get("offset") ?? "0");
        const ids = Array.from({ length: total }, (_, i) => total - i).slice(offset, offset + limit);
        return json(ids.map(task));
      }),
    );
    renderPage();

    await screen.findByText("第 1 / 2 頁・共 40 筆");
    total = 41; // 另一台剛簽了一筆

    await userEvent.click(screen.getByRole("button", { name: /下一頁/ }));

    // 換頁要重抓總數 → 看得到第 3 頁存在，而不是停在「共 40 筆、只有兩頁」
    await waitFor(() => expect(screen.getByText("第 2 / 3 頁・共 41 筆")).toBeDefined());
    expect(countCalls.length).toBeGreaterThan(1);
    expect(screen.getByRole("button", { name: /下一頁/ }).hasAttribute("disabled")).toBe(false);
  });
});
