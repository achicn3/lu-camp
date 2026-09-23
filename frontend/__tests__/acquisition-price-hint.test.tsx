// @vitest-environment jsdom
// 收購定價提示：同款以前收多少、賣多少。
//
// 這個元件唯一的職責是「把歷史講清楚，不要讓店員誤讀」，所以測的重點都在誤讀風險：
// 區間只看同型號不分成色（裁示 2026-09-22）、最近一次必須標成色、沒有成本不能顯示成 0。
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
      const payload = url.includes("/price-hint/records")
        ? { window_months: 12, used_all_time: false, total: 0, items: [] }
        : body;
      return new Response(JSON.stringify(payload), {
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
  it("區間只看同型號、不分成色（裁示 2026-09-22）", async () => {
    stubHint(A_AND_C);
    wrap(<PriceHint brandId={1} productModelId={2} />);

    await screen.findByText(/同型號以前收過 3 件/);
    expect(screen.getByText("歷史收購價區間")).toBeTruthy();
    expect(screen.getByText("20–45")).toBeTruthy();
    expect(screen.getByText("歷史上架售價區間")).toBeTruthy();
    expect(screen.getByText("70–130")).toBeTruthy();
    expect(screen.getByText(/近 12 個月/)).toBeTruthy();
    expect(screen.getByText(/含稅.*非成交價/)).toBeTruthy();
  });

  it("最近一次必須標成色——那件可能不是店員現在要收的成色", async () => {
    stubHint(A_AND_C);
    wrap(<PriceHint brandId={1} productModelId={2} />);

    // 上面講 A 級 35–45，這行講的卻是 C 級的 20；不標成色就會被讀成 A 級行情崩了。
    const latest = await screen.findByText(/最近一次收這款/);
    expect(latest.textContent).toContain("C 普通");
    expect(latest.textContent).toContain("收 20");
    expect(latest.textContent).toContain("上架 70");
  });

  it("展開後列出各成色，方便一眼比較", async () => {
    stubHint(A_AND_C);
    const user = userEvent.setup();
    wrap(<PriceHint brandId={1} productModelId={2} />);

    await user.click(await screen.findByRole("button", { name: /看各成色行情/ }));
    const table = screen.getByRole("table", { name: "各成色行情" });
    expect(table.textContent).toContain("A 近全新/精品");
    expect(table.textContent).toContain("C 普通");
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
    wrap(<PriceHint brandId={1} productModelId={2} />);

    await screen.findByText("150");
    expect(screen.getByText("無收購價記錄")).toBeTruthy();
    expect(screen.queryByText(/收購 0/)).toBeNull();
    expect(screen.getByText(/未填收購價/)).toBeTruthy();
  });

  it("退回全部歷史時要提醒行情可能已經變了", async () => {
    stubHint({ ...A_AND_C, used_all_time: true });
    wrap(<PriceHint brandId={1} productModelId={2} />);

    await screen.findByText(/近一年沒收過這款/);
  });

  it("查無歷史明示狀態，避免誤以為還在載入", async () => {
    stubHint({ window_months: 12, used_all_time: false, total_count: 0, grades: [], latest: null });
    wrap(<PriceHint brandId={1} productModelId={2} />);

    await waitFor(() => expect(requested.length).toBeGreaterThan(0));
    await screen.findByText(/尚無歷史記錄/);
  });

  it("讀取失敗明示可繼續估價，不冒充無歷史", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 500 })));
    wrap(<PriceHint brandId={1} productModelId={2} />);
    await screen.findByText(/歷史價格暫時無法讀取.*可繼續估價/);
    expect(screen.queryByText(/尚無歷史記錄/)).toBeNull();
  });

  it("同型號範圍忽略缺少成本的記錄，不把未知當零", async () => {
    stubHint({ ...A_AND_C, grades: [
      A_AND_C.grades[0],
      { ...A_AND_C.grades[1], cost_min: null, cost_max: null },
    ] });
    wrap(<PriceHint brandId={1} productModelId={2} />);
    await screen.findByText("35–45");
    expect(screen.queryByText("0–45")).toBeNull();
    expect(screen.getByText("70–130")).toBeTruthy();
  });

  it("切換型號不殘留前一款的區間", async () => {
    stubHint(A_AND_C);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const node = (model: number) => (
      <QueryClientProvider client={client}>
        <PriceHint brandId={1} productModelId={model} />
      </QueryClientProvider>
    );
    const { rerender } = render(node(2));
    await screen.findByText("20–45");
    stubHint({ ...A_AND_C, total_count: 0, grades: [], latest: null });
    rerender(node(3));
    expect(screen.queryByText("20–45")).toBeNull();
    await screen.findByText(/尚無歷史記錄/);
  });

  it("品牌或型號還沒選就不查——沒有可靠比對鍵時不要猜", async () => {
    stubHint(A_AND_C);
    wrap(<PriceHint brandId={1} productModelId={null} />);

    await waitFor(() => expect(requested).toHaveLength(0));
    expect(screen.queryByText(/以前收過/)).toBeNull();
  });
});

// ── 一般行情＋最近 5 筆＋整年紀錄（2026-09-23 裁示）──────────────────────────

const MANY = {
  window_months: 12,
  used_all_time: false,
  total_count: 25,
  typical: { cost_low: "1100", cost_high: "1400", listed_low: "2300", listed_high: "2900" },
  grades: [
    { grade: "S", count: 5, cost_min: "1500", cost_max: "2400", listed_min: "2900", listed_max: "9000" },
    { grade: "B", count: 20, cost_min: "300", cost_max: "1400", listed_min: "1680", listed_max: "2900" },
  ],
  latest: { acquired_at: "2026-09-18T05:00:00Z", grade: "B", cost: "1200", listed_price: "2500" },
};

function record(i: number) {
  return {
    acquired_at: `2026-09-${String(20 - (i % 19)).padStart(2, "0")}T05:00:00Z`,
    grade: "B",
    cost: String(1000 + i),
    listed_price: String(2000 + i),
    status: i === 1 ? "SOLD" : "IN_STOCK",
  };
}

/** 彙總與逐筆分開回；逐筆依 limit/offset 切 total 筆。 */
function stubWithRecords(hint: typeof MANY, total = hint.total_count) {
  requested = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      requested.push(url);
      let body: unknown = hint;
      if (url.includes("/price-hint/records")) {
        const q = new URL(url).searchParams;
        const limit = Number(q.get("limit") ?? 20);
        const offset = Number(q.get("offset") ?? 0);
        const items = Array.from({ length: Math.max(0, Math.min(limit, total - offset)) }, (_, k) =>
          record(offset + k),
        );
        body = { window_months: 12, used_all_time: hint.used_all_time, total, items };
      }
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

describe("一般行情與逐筆紀錄", () => {
  it("有一般行情時放最上面，最低～最高退成一行參考", async () => {
    stubWithRecords(MANY);
    wrap(<PriceHint brandId={1} productModelId={2} />);

    await screen.findByText("一般收購價");
    expect(screen.getByText("1,100–1,400")).toBeTruthy();
    expect(screen.getByText("一般上架售價")).toBeTruthy();
    expect(screen.getByText("2,300–2,900")).toBeTruthy();
    const extremes = screen.getByText(/最低～最高/);
    expect(extremes.textContent).toContain("300–2,400");
    expect(extremes.textContent).toContain("1,680–9,000");
    expect(screen.queryByText("歷史收購價區間")).toBeNull();
  });

  it("展開後列出最近 5 筆（含是否已售出），只查 5 筆", async () => {
    stubWithRecords(MANY);
    const user = userEvent.setup();
    wrap(<PriceHint brandId={1} productModelId={2} />);

    await user.click(await screen.findByRole("button", { name: /看各成色行情與最近紀錄/ }));
    const recent = await screen.findByRole("table", { name: "最近 5 筆" });
    await waitFor(() => expect(recent.querySelectorAll("tbody tr")).toHaveLength(5));
    expect(recent.textContent).toContain("1,000");
    expect(recent.textContent).toContain("已售出");
    const recordsUrl = requested.find((u) => u.includes("/price-hint/records"));
    expect(recordsUrl).toContain("limit=5");
  });

  it("可以翻看近一年全部收購紀錄", async () => {
    stubWithRecords(MANY);
    const user = userEvent.setup();
    wrap(<PriceHint brandId={1} productModelId={2} />);

    await user.click(await screen.findByRole("button", { name: /看各成色行情與最近紀錄/ }));
    await user.click(await screen.findByRole("button", { name: "看近一年全部 25 筆收購紀錄" }));
    const all = await screen.findByRole("table", { name: "全部收購紀錄" });
    await waitFor(() => expect(all.querySelectorAll("tbody tr")).toHaveLength(20));
    expect(screen.getByText("第 1 / 2 頁")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "下一頁" }));
    await waitFor(() =>
      expect(
        screen.getByRole("table", { name: "全部收購紀錄" }).querySelectorAll("tbody tr"),
      ).toHaveLength(5),
    );
    expect(requested.some((u) => u.includes("offset=20"))).toBe(true);
    expect(screen.getByText("第 2 / 2 頁")).toBeTruthy();
  });

  it("5 筆以內不必再給「看全部」", async () => {
    stubWithRecords({ ...MANY, total_count: 4 }, 4);
    const user = userEvent.setup();
    wrap(<PriceHint brandId={1} productModelId={2} />);

    await user.click(await screen.findByRole("button", { name: /看各成色行情與最近紀錄/ }));
    await screen.findByRole("table", { name: "最近 5 筆" });
    expect(screen.queryByRole("button", { name: /全部.*筆收購紀錄/ })).toBeNull();
  });

  it("退回全部歷史時，看全部的按鈕不說「近一年」", async () => {
    stubWithRecords({ ...MANY, used_all_time: true });
    const user = userEvent.setup();
    wrap(<PriceHint brandId={1} productModelId={2} />);

    await user.click(await screen.findByRole("button", { name: /看各成色行情與最近紀錄/ }));
    expect(await screen.findByRole("button", { name: "看全部 25 筆收購紀錄" })).toBeTruthy();
  });

  it("翻頁資料還沒回來時，頁碼講讀取中、按鈕鎖住，不讓頁碼與內容對不上", async () => {
    stubWithRecords(MANY);
    const fastFetch = globalThis.fetch;
    let release: () => void = () => {};
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("offset=20")) await gate;
        return fastFetch(input);
      }),
    );
    const user = userEvent.setup();
    wrap(<PriceHint brandId={1} productModelId={2} />);

    await user.click(await screen.findByRole("button", { name: /看各成色行情與最近紀錄/ }));
    await user.click(await screen.findByRole("button", { name: "看近一年全部 25 筆收購紀錄" }));
    await screen.findByText("第 1 / 2 頁");
    await user.click(screen.getByRole("button", { name: "下一頁" }));

    expect(await screen.findByText("第 2 頁讀取中…")).toBeTruthy();
    expect(screen.queryByText("第 2 / 2 頁")).toBeNull();
    expect((screen.getByRole("button", { name: "上一頁" }) as HTMLButtonElement).disabled).toBe(true);

    release();
    await screen.findByText("第 2 / 2 頁");
  });
});
