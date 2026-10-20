// @vitest-environment jsdom
// 線上 LINE Pay 開關（店主 2026-10-20）：管理者在「線上發布」打開／關掉，雲端生效；預設關。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { OnlineLinePaySwitch } from "@/features/menu/OnlineLinePaySwitch";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function stub(state: { linepay_enabled: boolean | null; linepay_configured: boolean | null }) {
  const puts: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(String(input), init);
      if (request.method === "PUT") {
        const body = (await request.json()) as { enabled: boolean };
        puts.push(body);
        state.linepay_enabled = body.enabled;
      }
      return Response.json({ orders: [], accepting: true, ...state });
    }),
  );
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <OnlineLinePaySwitch />
    </QueryClientProvider>,
  );
  return puts;
}

it("顯示現況，勾了就送到雲端", async () => {
  const puts = stub({ linepay_enabled: false, linepay_configured: true });
  const box = (await screen.findByRole("checkbox", { name: "線上點餐開放 LINE Pay" })) as HTMLInputElement;
  await waitFor(() => expect(box.checked).toBe(false));
  await userEvent.setup().click(box);
  await waitFor(() => expect(puts).toEqual([{ enabled: true }]));
  await waitFor(() => expect(box.checked).toBe(true));
  expect(screen.getByText("客人可以在手機上用 LINE Pay 付款。")).toBeTruthy();
});

it("雲端沒有金鑰：說明打開也不會生效", async () => {
  stub({ linepay_enabled: true, linepay_configured: false });
  expect(await screen.findByText("雲端還沒設定 LINE Pay 金鑰，打開也不會生效。")).toBeTruthy();
});

it("還沒跟雲端連上：先不能切", async () => {
  stub({ linepay_enabled: null, linepay_configured: null });
  const box = (await screen.findByRole("checkbox", { name: "線上點餐開放 LINE Pay" })) as HTMLInputElement;
  await waitFor(() => expect(screen.getByText("還沒跟雲端連上，連上後才能切換。")).toBeTruthy());
  expect(box.disabled).toBe(true);
});
