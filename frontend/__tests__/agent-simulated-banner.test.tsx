// @vitest-environment jsdom
// 假裝模式橫幅：代理沒接真機時，畫面必須主動承認「列印不會出紙」。
//
// 這是「螢幕說已送出、紙卻沒出來」的最後一道防線。前兩道在代理端（開機讀 .env、
// 模式沒設就拒絕啟動），但都擋不住「有人刻意設成 fake 又忘了改回來」——那時列印
// 一樣回成功。所以畫面要在**印之前**就講，而不是等店員拿著空手才發現。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
}));

import AuthedLayout from "@/app/(authed)/layout";
import { readTokenRole } from "@/lib/auth";
import { clearToken, setToken } from "@/lib/token";

function makeToken(role: string, storeId = 1, sub = "1"): string {
  const b64url = (obj: unknown) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${b64url({ alg: "HS256", typ: "JWT" })}.${b64url({ sub, role, store_id: storeId })}.sig`;
}

/** 依 URL 分流：代理 /health 回指定內容，其餘（/auth/me 等）回一般身分。 */
function stubFetch(agent: { ok: boolean; body?: unknown }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown) => {
      const url = String(input);
      if (url.includes("/health")) {
        if (!agent.ok) throw new TypeError("Failed to fetch");
        return new Response(JSON.stringify(agent.body), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(
        JSON.stringify({ id: 1, role: readTokenRole() ?? "CLERK", store_id: 1 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }),
  );
}

function renderLayout(children: ReactNode) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthedLayout>{children}</AuthedLayout>
    </QueryClientProvider>,
  );
}

const BANNER = /測試模式/;

beforeEach(() => {
  setToken(makeToken("CLERK"));
});

afterEach(() => {
  cleanup();
  clearToken();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("假裝模式橫幅", () => {
  it("代理回報假裝模式 → 橫幅講明列印不會出紙", async () => {
    stubFetch({ ok: true, body: { status: "ok", simulated: true } });
    renderLayout(<p>受保護內容</p>);
    await screen.findByText("受保護內容");

    const banner = await screen.findByText(BANNER);
    expect(banner.textContent).toMatch(/不會|沒有/); // 要說出後果，不只說模式名稱
  });

  it("代理接的是真機 → 不得出現橫幅", async () => {
    stubFetch({ ok: true, body: { status: "ok", simulated: false } });
    renderLayout(<p>受保護內容</p>);
    await screen.findByText("受保護內容");

    await waitFor(() => expect(screen.queryByText(BANNER)).toBeNull());
  });

  it("代理連不上 → 不得誤報假裝模式", async () => {
    // 代理離線是另一回事（列印時會明確報錯）；這裡若誤顯示橫幅，等於天天亮著，
    // 店員很快就學會無視，真的進假裝模式時也不會有人看。
    stubFetch({ ok: false });
    renderLayout(<p>受保護內容</p>);
    await screen.findByText("受保護內容");

    await waitFor(() => expect(screen.queryByText(BANNER)).toBeNull());
  });
});
