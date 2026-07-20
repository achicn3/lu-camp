// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "1" }),
}));

import MemberDetailPage from "@/app/(authed)/contacts/[id]/page";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("會員資料頁", () => {
  it("返回會員列表使用標準次要按鈕樣式與完整標籤", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    render(<MemberDetailPage />, { wrapper });

    const back = screen.getByRole("link", { name: "返回會員列表" });
    expect(back.getAttribute("href")).toBe("/contacts");
    expect(back.classList.contains("btn-secondary")).toBe(true);
    expect(back.classList.contains("member-back-link")).toBe(true);
  });
});
