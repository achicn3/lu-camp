// @vitest-environment jsdom
// 收購頁的作廢入口（2026-09-23 裁示）：舊的「輸入單號作廢」拿掉，改放連到收購紀錄的連結——
// 作廢只剩收購紀錄清單一個入口（該頁的作廢鈕限管理者，後端 ManagerDep 為最終權威）。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

const auth = vi.hoisted(() => ({ role: "MANAGER" as "MANAGER" | "CLERK" }));
vi.mock("@/lib/auth", () => ({
  decodeSession: () => ({ userId: 1, role: auth.role, storeId: 1 }),
  logout: vi.fn(),
}));

import AcquisitionPage from "@/app/(authed)/acquisition/page";

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.includes("/settings")) return json({ premium_rate: "0.1000", default_margin_pct: 45 });
      if (url.includes("/categories")) return json([]);
      if (url.includes("/cash-sessions/current")) return json({ id: 1, status: "OPEN" });
      return json([]);
    }),
  );
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<AcquisitionPage />, { wrapper });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("收購頁的作廢入口改到收購紀錄", () => {
  it("管理者：不再有輸入單號作廢，改成連到收購紀錄", async () => {
    auth.role = "MANAGER";
    stubFetch();
    renderPage();
    const link = await screen.findByRole("link", { name: /收購紀錄/ });
    expect(link.getAttribute("href")).toBe("/acquisition/records");
    expect(screen.queryByText("作廢收購（限管理者）")).toBeNull();
    expect(screen.queryByLabelText("收購單號")).toBeNull();
  });

  it("店員也看得到收購紀錄連結（清單店員可看）", async () => {
    auth.role = "CLERK";
    stubFetch();
    renderPage();
    await waitFor(() => expect(screen.getByText("收購鑑價入庫")).toBeTruthy());
    expect(screen.getByRole("link", { name: /收購紀錄/ })).toBeTruthy();
    expect(screen.queryByText("作廢收購（限管理者）")).toBeNull();
  });
});
