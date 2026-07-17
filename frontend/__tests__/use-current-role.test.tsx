// @vitest-environment jsdom
// useCurrentRole：/auth/me 為權威 DB 現值角色；失敗時 fail-closed（不回退 stale JWT）。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setToken } from "@/lib/token";
import { useCurrentRole } from "@/lib/useCurrentRole";

function makeToken(role: string): string {
  const b64url = (obj: unknown) =>
    btoa(JSON.stringify(obj)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${b64url({ alg: "HS256", typ: "JWT" })}.${b64url({ sub: "1", role, store_id: 1 })}.sig`;
}

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  Wrapper.displayName = "TestQueryWrapper";
  return Wrapper;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useCurrentRole fail-closed", () => {
  it("/auth/me 回 MANAGER → isManager true（權威 DB 角色）", async () => {
    setToken(makeToken("CLERK")); // JWT 說 CLERK，DB 說 MANAGER（升權未重登）
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ id: 1, role: "MANAGER", store_id: 1 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    const { result } = renderHook(() => useCurrentRole(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isManager).toBe(true));
  });

  it("/auth/me 500 失敗 → fail-closed，即使 JWT 說 MANAGER 也不顯管理（降權中斷情境）", async () => {
    setToken(makeToken("MANAGER")); // JWT 仍說 MANAGER，但已被降權且 /auth/me 中斷
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("err", { status: 500 })),
    );
    const { result } = renderHook(() => useCurrentRole(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.roleUnavailable).toBe(true));
    expect(result.current.isManager).toBe(false); // 不回退 stale JWT 的 MANAGER
    expect(result.current.role).toBeNull();
  });
});
