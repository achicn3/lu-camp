// @vitest-environment jsdom
// 「每天第一次自動帶到開店前檢查」的行為（裁示 2026-09-17）：一天只帶一次、不擋操作。
// 這是裁示的核心，必須被測試釘住——只靠肉眼看煙霧驗不到「第二次不再導」。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const replaceMock = vi.fn();
let pathname = "/";
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
  usePathname: () => pathname,
}));

import AuthedLayout from "@/app/(authed)/layout";
import { clearToken, setToken } from "@/lib/token";

function makeToken(role = "MANAGER"): string {
  const b64url = (obj: unknown) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${b64url({ alg: "HS256", typ: "JWT" })}.${b64url({ sub: "1", role, store_id: 1 })}.sig`;
}

const TODAY_INCOMPLETE = {
  business_date: "2026-09-17",
  cash_session_state: "NONE",
  cash_session_open: false,
  items: [],
  skipped_keys: [],
  completed: false,
};

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(today: unknown = TODAY_INCOMPLETE) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      if (url.includes("/opening-check/today")) return json(today);
      if (url.includes("/devices/status")) return json({ devices: [] });
      if (url.includes("/auth/me")) return json({ id: 1, role: "MANAGER", store_id: 1 });
      return json({});
    }),
  );
}

function renderLayout(children: ReactNode = <p>內容</p>) {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AuthedLayout>{children}</AuthedLayout>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  pathname = "/";
  window.localStorage.clear();
  stubFetch();
});

afterEach(() => {
  cleanup();
  clearToken();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("開店前檢查的自動導向", () => {
  it("當天第一次進系統：帶到檢查頁", async () => {
    setToken(makeToken());
    renderLayout();
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/opening-check"));
  });

  it("同一天第二次：不再帶（不擋操作，只跳一次）", async () => {
    setToken(makeToken());
    window.localStorage.setItem("lu-camp.opening-check.2026-09-17", "1");
    renderLayout();
    await screen.findByText("內容");
    expect(replaceMock).not.toHaveBeenCalledWith("/opening-check");
  });

  it("已經在檢查頁上：不重複導向（免得洗掉瀏覽紀錄）", async () => {
    pathname = "/opening-check";
    setToken(makeToken());
    renderLayout();
    await screen.findByText("內容");
    expect(replaceMock).not.toHaveBeenCalledWith("/opening-check");
  });

  it("localStorage 被封鎖（無痕/隱私設定）：不導向，免得每次換頁都被拉走", async () => {
    setToken(makeToken());
    const original = window.localStorage.getItem;
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    renderLayout();
    await screen.findByText("內容");
    expect(replaceMock).not.toHaveBeenCalledWith("/opening-check");
    vi.mocked(Storage.prototype.getItem).mockRestore();
    expect(typeof original).toBe("function");
  });

  it("今天已完成：不帶、也不顯示紅點", async () => {
    stubFetch({ ...TODAY_INCOMPLETE, completed: true });
    setToken(makeToken());
    renderLayout();
    await screen.findByText("內容");
    expect(replaceMock).not.toHaveBeenCalledWith("/opening-check");
    expect(document.querySelector(".nav-dot")).toBeNull();
  });

  it("讀不到今日狀態時不顯示紅點、也不亂導（錯誤另外報）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("/opening-check/today")) {
          return new Response(JSON.stringify({ detail: "boom" }), { status: 500 });
        }
        if (url.includes("/devices/status")) return json({ devices: [] });
        if (url.includes("/auth/me")) return json({ id: 1, role: "MANAGER", store_id: 1 });
        return json({});
      }),
    );
    setToken(makeToken());
    renderLayout();
    await screen.findByText("內容");
    expect(replaceMock).not.toHaveBeenCalledWith("/opening-check");
    expect(document.querySelector(".nav-dot")).toBeNull();
  });
});
