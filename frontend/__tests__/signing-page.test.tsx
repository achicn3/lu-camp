// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import SigningPage from "@/app/(authed)/signing/page";

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <SigningPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("/signing", () => {
  it("狀態與類型使用網站一致的篩選下拉樣式", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    renderPage();

    const status = screen.getByLabelText("狀態");
    const kind = screen.getByLabelText("類型");
    expect(status.classList.contains("signing-filter-select")).toBe(true);
    expect(kind.classList.contains("signing-filter-select")).toBe(true);
  });
});
