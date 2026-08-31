// @vitest-environment jsdom
// 列印回應已經帶著「這次是假的」，不該丟掉：等下一輪輪詢（最長 60 秒）才亮橫幅，
// 中間那幾單店員照樣以為印出來了。收到就立刻讓橫幅重新查一次（Codex 審查中風險項）。
import { afterEach, describe, expect, it, vi } from "vitest";

import { AGENT_SIMULATED_EVENT, openCashDrawer, printLabel } from "@/lib/agent";

function stubPost(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  );
}

function countEvents(): () => number {
  let n = 0;
  const on = () => {
    n += 1;
  };
  window.addEventListener(AGENT_SIMULATED_EVENT, on);
  return () => n;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("列印回應中的假裝註記", () => {
  it("回應說這次是假的 → 立刻通知畫面重查", async () => {
    stubPost({ status: "ok", simulated: true });
    const fired = countEvents();

    await printLabel("SN-1", "帳篷", 100);

    expect(fired()).toBe(1);
  });

  it("回應說真的印了 → 不得發出通知（誤報會讓橫幅變雜訊）", async () => {
    stubPost({ status: "ok", simulated: false });
    const fired = countEvents();

    await printLabel("SN-1", "帳篷", 100);

    expect(fired()).toBe(0);
  });

  it("舊版代理沒有這個欄位 → 不得誤判", async () => {
    stubPost({ status: "ok" });
    const fired = countEvents();

    await openCashDrawer();

    expect(fired()).toBe(0);
  });
});
