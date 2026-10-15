// @vitest-environment jsdom
// 桌號 QR 一鍵列印（店主 2026-10-15 B.1）：每桌一張卡（店名、桌號、QR、掃碼說明），可選要印哪幾桌。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TableQrSheet } from "@/features/menu/TableQrSheet";

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
}
function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}
const STATUS = {
  configured: true,
  store_name: "露坑",
  last_version: 1790900000000,
  last_published_at: "2026-10-02T03:00:00Z",
  tables: [
    { label: "A1", service_mode: "DINE_IN", code: "c1".repeat(11), url: "https://order.test/t/c1c1" },
    { label: "A2", service_mode: "DINE_IN", code: "c3".repeat(11), url: "https://order.test/t/c3c3" },
    { label: "外帶", service_mode: "TAKEOUT", code: "c2".repeat(11), url: "https://order.test/t/c2c2" },
  ],
};
function stub(body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => json(body)));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("桌號 QR 列印", () => {
  it("每桌一張卡：店名、桌號、QR、掃碼說明；外帶寫外帶", async () => {
    stub(STATUS);
    wrap(<TableQrSheet />);
    const card = await screen.findByRole("article", { name: "桌號 A1 點餐 QR" });
    expect(within(card).getByText("露坑")).toBeTruthy();
    expect(within(card).getByText("桌號 A1")).toBeTruthy();
    expect(within(card).getByText("手機掃描，直接點餐")).toBeTruthy();
    expect(within(card).getByRole("img", { name: "A1 的點餐 QR" }).querySelector("path")?.getAttribute("d")).toMatch(/^M\d/);
    expect(within(card).getByText("https://order.test/t/c1c1")).toBeTruthy();
    expect(screen.getByRole("article", { name: "外帶 點餐 QR" })).toBeTruthy();
    expect(screen.getAllByRole("article")).toHaveLength(3);
  });

  it("指定一桌（重發後重印）：只印那桌，可再勾別桌", async () => {
    stub(STATUS);
    const user = userEvent.setup();
    wrap(<TableQrSheet initial="A2" />);
    await screen.findByRole("article", { name: "桌號 A2 點餐 QR" });
    expect(screen.getAllByRole("article")).toHaveLength(1);
    await user.click(screen.getByRole("checkbox", { name: "A1" }));
    expect(screen.getAllByRole("article")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "列印 2 張" })).toBeTruthy();
  });

  it("按列印叫出瀏覽器列印；一張都沒選就不能按", async () => {
    stub(STATUS);
    const print = vi.spyOn(window, "print").mockImplementation(() => {});
    const user = userEvent.setup();
    wrap(<TableQrSheet />);
    await user.click(await screen.findByRole("button", { name: "列印 3 張" }));
    expect(print).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "全部不選" }));
    expect((screen.getByRole("button", { name: "列印 0 張" }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole("button", { name: "全選" }));
    expect(screen.getAllByRole("article")).toHaveLength(3);
  });

  it("還沒發佈過：提醒先發佈，QR 才掃得開", async () => {
    stub({ ...STATUS, last_version: null, last_published_at: null });
    wrap(<TableQrSheet />);
    expect(await screen.findByText("還沒發佈過線上點餐：請先回上一頁按「發佈到線上點餐」，印出來的 QR 才掃得開。")).toBeTruthy();
  });

  it("沒設定雲端網址或沒有桌號：說明原因、不給印", async () => {
    stub({ ...STATUS, configured: false, tables: [] });
    wrap(<TableQrSheet />);
    expect(await screen.findByText("尚未設定線上點餐的雲端網址，印不出 QR。請聯絡管理者。")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /列印/ })).toBeNull();
    cleanup();
    stub({ ...STATUS, tables: [] });
    wrap(<TableQrSheet />);
    expect(await screen.findByText("還沒有桌號：請先到「設定」加內用桌號，再發佈到線上點餐。")).toBeTruthy();
  });
});
