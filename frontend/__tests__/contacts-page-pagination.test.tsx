// @vitest-environment jsdom
// 會員清單的分頁：總筆數讀失敗時**不可**沿用上一次成功的舊值。
// 舊值會讓「剛好滿一頁」看起來只有一頁，剛建檔的那個人就再也翻不到（Codex 審查指出）。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import ContactsPage from "@/app/(authed)/contacts/page";
import { clearToken, setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function member(id: number) {
  return {
    id,
    name: `會員${id}`,
    phone: `09000000${String(id).padStart(2, "0")}`,
    roles: ["MEMBER"],
    member_points: 0,
    store_credit_balance: "0",
    national_id_masked: null,
    address: null,
    source_note: null,
    created_at: "2026-09-12T02:00:00Z",
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<ContactsPage />, { wrapper });
}

afterEach(() => {
  cleanup();
  clearToken();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("/contacts 會員分頁", () => {
  it("建檔後總筆數重抓失敗時，不沿用舊總數把新會員關在「只有一頁」後面", async () => {
    setToken(fakeJwt({ sub: "1", role: "MANAGER", store_id: 1 }));
    let total = 50; // 剛好一頁
    let countWorks = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
        const json = (data: unknown, status = 200) =>
          new Response(JSON.stringify(data), {
            status,
            headers: { "Content-Type": "application/json" },
          });
        if (method === "POST") {
          total = 51; // 建檔成功 → 多一個人，清單變成兩頁
          countWorks = false; // 但這一刻總數端點壞了
          return json(member(51), 201);
        }
        if (url.pathname.endsWith("/members/count")) {
          return countWorks ? json({ count: total }) : json({ detail: "壞掉了" }, 500);
        }
        const limit = Number(url.searchParams.get("limit") ?? "50");
        const offset = Number(url.searchParams.get("offset") ?? "0");
        const ids = Array.from({ length: total }, (_, i) => i + 1).slice(offset, offset + limit);
        return json(ids.map(member));
      }),
    );
    renderPage();

    await userEvent.click(screen.getByRole("button", { name: "所有會員" }));
    // 50 人剛好一頁：分頁控制不顯示。
    await waitFor(() => expect(screen.getAllByRole("row").length).toBe(51));
    expect(screen.queryByRole("button", { name: /下一頁/ })).toBeNull();

    await userEvent.type(screen.getByLabelText("姓名 *"), "新來的");
    await userEvent.type(screen.getByLabelText("電話 *"), "0900123456");
    await userEvent.click(screen.getByRole("button", { name: "建檔" }));

    // 總數讀不到 → 退回「滿頁即可能有下一頁」，第 51 人翻得到；
    // 沿用舊的 50 會讓分頁整個消失，那個人就不見了。
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /下一頁/ }).hasAttribute("disabled")).toBe(false),
    );
  });
});
