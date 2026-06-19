// @vitest-environment jsdom
// F6.5 作廢入口角色閘：管理者於收購頁可見「作廢收購（限管理者）」查詢區；店員看不到。
// （後端 ManagerDep 為最終權威；此測試只驗前端 UX 隱藏。）
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

describe("作廢收購入口角色閘", () => {
  it("管理者可見作廢查詢區", async () => {
    auth.role = "MANAGER";
    stubFetch();
    renderPage();
    expect(await screen.findByText("作廢收購（限管理者）")).toBeTruthy();
  });

  it("店員看不到作廢查詢區", async () => {
    auth.role = "CLERK";
    stubFetch();
    renderPage();
    await waitFor(() => expect(screen.getByText("收購鑑價入庫")).toBeTruthy());
    expect(screen.queryByText("作廢收購（限管理者）")).toBeNull();
  });
});
