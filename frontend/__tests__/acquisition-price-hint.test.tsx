// @vitest-environment jsdom
// 收購定價提示：同款以前收多少、賣多少。
//
// 這個元件唯一的職責是「把歷史講清楚，不要讓店員誤讀」，所以測的重點都在誤讀風險：
// 沒收過的成色不能拿別級距的價唬人、最近一次必須標成色、沒有成本不能顯示成 0。
// 寄售已在後端整批排除（裁示 2026-09-09），前端不需要也不應該再處理寄售。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

// 本專案沒有掛 @testing-library/jest-dom，斷言一律用 vitest 內建的真值/null 判斷。

import { PriceHint } from "@/features/acquisition/PriceHint";
import { setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

const A_AND_C = {
  window_months: 12,
  used_all_time: false,
  total_count: 3,
  grades: [
    { grade: "A", count: 2, cost_min: "35", cost_max: "45", listed_min: "100", listed_max: "130" },
    { grade: "C", count: 1, cost_min: "20", cost_max: "20", listed_min: "70", listed_max: "70" },
  ],
  latest: {
    acquired_at: "2026-09-09T05:00:00Z",
    grade: "C",
    cost: "20",
    listed_price: "70",
  },
};

let requested: string[] = [];

function stubHint(body: unknown) {
  requested = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      requested.push(url);
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

setToken(fakeJwt({ sub: "1", role: "CLERK", store_id: 1 }));

describe("收購定價提示", () => {
  it("選定成色後顯示該成色的收購價與售價區間", async () => {
    stubHint(A_AND_C);
    wrap(<PriceHint brandId={1} productModelId={2} grade="A" />);

    await screen.findByText(/以前收過 2 件/);
    expect(screen.getByText(/收購 35–45、售價 100–130/)).toBeTruthy();
  });

  it("最近一次必須標成色——那件可能不是店員現在要收的成色", async () => {
    stubHint(A_AND_C);
    wrap(<PriceHint brandId={1} productModelId={2} grade="A" />);

    // 上面講 A 級 35–45，這行講的卻是 C 級的 20；不標成色就會被讀成 A 級行情崩了。
    const latest = await screen.findByText(/最近一次收這款/);
    expect(latest.textContent).toContain("C 有使用痕跡");
    expect(latest.textContent).toContain("收 20");
    expect(latest.textContent).toContain("賣 70");
  });

  it("沒收過的成色要明說，不能拿別級距的價唬人", async () => {
    stubHint(A_AND_C);
    wrap(<PriceHint brandId={1} productModelId={2} grade="S" />);

    await screen.findByText(/但沒收過 S 全新\/未使用/);
    expect(screen.queryByText(/收購 35–45/)).toBeNull();
  });

  it("展開後列出各成色，方便一眼比較", async () => {
    stubHint(A_AND_C);
    const user = userEvent.setup();
    wrap(<PriceHint brandId={1} productModelId={2} grade="A" />);

    await user.click(await screen.findByRole("button", { name: /看各成色行情/ }));
    const table = screen.getByRole("table");
    expect(table.textContent).toContain("A 近全新/精品");
    expect(table.textContent).toContain("C 有使用痕跡");
  });

  it("沒有收購價紀錄時只講售價，不得補 0 唬人", async () => {
    stubHint({
      window_months: 12,
      used_all_time: false,
      total_count: 1,
      grades: [
        { grade: "A", count: 1, cost_min: null, cost_max: null, listed_min: "150", listed_max: "150" },
      ],
      latest: { acquired_at: "2026-09-01T05:00:00Z", grade: "A", cost: null, listed_price: "150" },
    });
    wrap(<PriceHint brandId={1} productModelId={2} grade="A" />);

    await screen.findByText(/售價 150/);
    expect(screen.queryByText(/收購 0/)).toBeNull();
    expect(screen.getByText(/未填收購價/)).toBeTruthy();
  });

  it("退回全部歷史時要提醒行情可能已經變了", async () => {
    stubHint({ ...A_AND_C, used_all_time: true });
    wrap(<PriceHint brandId={1} productModelId={2} grade="A" />);

    await screen.findByText(/近一年沒收過這款/);
  });

  it("查無歷史就整個不顯示，不要用「查無資料」佔畫面", async () => {
    stubHint({ window_months: 12, used_all_time: false, total_count: 0, grades: [], latest: null });
    const { container } = wrap(<PriceHint brandId={1} productModelId={2} grade="A" />);

    await waitFor(() => expect(requested.length).toBeGreaterThan(0));
    expect(container.querySelector(".price-hint")).toBeNull();
  });

  it("品牌或型號還沒選就不查——沒有可靠比對鍵時不要猜", async () => {
    stubHint(A_AND_C);
    wrap(<PriceHint brandId={1} productModelId={null} grade="A" />);

    await waitFor(() => expect(requested).toHaveLength(0));
    expect(screen.queryByText(/以前收過/)).toBeNull();
  });
});
