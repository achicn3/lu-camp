// @vitest-environment jsdom
// (authed) 守衛測試：有效店務 token 渲染內容；無 token 導回 /login；KIOSK/無效 token 不渲染
// 店務殼（導回 /kiosk 或 /login）；401 廣播導回；登出/401 清空 query 快取。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
}));

import AuthedLayout from "@/app/(authed)/layout";
import { readTokenRole } from "@/lib/auth";
import { UNAUTHORIZED_EVENT, clearToken, setToken } from "@/lib/token";

// 產生 decodeSession 可解析的 JWT 形狀 token（header.payload.sig，payload 為 base64url）。
function makeToken(role: string, storeId = 1, sub = "1"): string {
  const b64url = (obj: unknown) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${b64url({ alg: "HS256", typ: "JWT" })}.${b64url({ sub, role, store_id: storeId })}.sig`;
}

function renderLayout(children: ReactNode, queryClient = new QueryClient()) {
  render(
    <QueryClientProvider client={queryClient}>
      <AuthedLayout>{children}</AuthedLayout>
    </QueryClientProvider>,
  );
  return queryClient;
}

beforeEach(() => {
  // useCurrentRole 會打真實 /auth/me；測試需回目前 token 的 DB 角色，避免本機剛好有 backend
  // 時假 token 收到 401、觸發全域登出，造成與環境相依的 flaky。
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({ id: 1, role: readTokenRole() ?? "CLERK", store_id: 1 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  );
});

afterEach(() => {
  cleanup();
  clearToken();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("(authed) layout", () => {
  it("有效店務 token：渲染內容、不導向", async () => {
    setToken(makeToken("MANAGER"));
    renderLayout(<p>受保護內容</p>);
    expect(await screen.findByText("受保護內容")).toBeDefined();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("導覽分層：系統選單固定為左上第一個控制，常用功能留在頂欄", async () => {
    setToken(makeToken("MANAGER"));
    renderLayout(<p>受保護內容</p>);
    await screen.findByText("受保護內容");
    const nav = screen.getByRole("navigation", { name: "主要導覽" });
    const menuButton = within(nav).getByRole("button", { name: "開啟系統選單" });
    expect(nav.firstElementChild).toBe(menuButton);
    // 常用項目常駐頂欄
    expect(screen.getByText("POS 結帳")).toBeDefined();
    expect(screen.getByText("交易紀錄")).toBeDefined();
    // 次要項目收在選單，未開啟前不在畫面
    expect(screen.queryByText("庫存")).toBeNull();
    // 點左上選單開啟左側抽屜 → 次要項目出現
    await userEvent.click(menuButton);
    expect(screen.getByRole("navigation", { name: "系統選單" })).toBeDefined();
    expect(screen.getByText("庫存")).toBeDefined();
    // 關閉 → 次要項目收回
    await userEvent.click(screen.getByRole("button", { name: "關閉選單" }));
    await waitFor(() => expect(screen.queryByText("庫存")).toBeNull());
  });

  it("token 只在 localStorage（重新整理情境）：仍渲染內容、不誤導去登入", async () => {
    window.localStorage.setItem("lu-camp.access-token", makeToken("CLERK"));
    renderLayout(<p>受保護內容</p>);
    expect(await screen.findByText("受保護內容")).toBeDefined();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("無 token：不渲染內容、導回 /login", async () => {
    renderLayout(<p>受保護內容</p>);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("受保護內容")).toBeNull();
  });

  it("KIOSK token：不渲染店務殼、導回 /kiosk", async () => {
    setToken(makeToken("KIOSK"));
    renderLayout(<p>受保護內容</p>);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/kiosk"));
    expect(screen.queryByText("受保護內容")).toBeNull();
  });

  it("無效（非 JWT）token：不渲染店務殼、導回 /login", async () => {
    setToken("garbage");
    renderLayout(<p>受保護內容</p>);
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("受保護內容")).toBeNull();
  });

  it("收到 401 廣播：導回 /login 並清空 query 快取", async () => {
    setToken(makeToken("MANAGER"));
    const qc = new QueryClient();
    qc.setQueryData(["reports", "access"], "granted");
    renderLayout(<p>受保護內容</p>, qc);
    await screen.findByText("受保護內容");
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    // auth-me 是仍掛載的常駐 observer，clear 後可能立即重建；核心要求是前一身分的業務快取消失。
    expect(qc.getQueryData(["reports", "access"])).toBeUndefined();
  });

  it("登出：清空 query 快取，避免下一位登入者看到前一身分的資料/授權", async () => {
    setToken(makeToken("MANAGER"));
    const qc = new QueryClient();
    qc.setQueryData(["premium-rate-history"], [{ id: 1 }]);
    qc.setQueryData(["reports", "access"], "granted");
    renderLayout(<p>受保護內容</p>, qc);
    await screen.findByText("受保護內容");
    await userEvent.click(screen.getByRole("button", { name: "登出" }));
    // 前一身分的授權/資料須清空（auth-me 為常駐 observer、登出後 disabled 且只會重取當前身分，
    // 不算陳舊資料，故不以總長度 0 斷言）。
    expect(qc.getQueryData(["premium-rate-history"])).toBeUndefined();
    expect(qc.getQueryData(["reports", "access"])).toBeUndefined();
    expect(replaceMock).toHaveBeenCalledWith("/login");
  });
});
