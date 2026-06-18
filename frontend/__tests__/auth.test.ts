// @vitest-environment jsdom
// lib/auth 單元測試：token 存取（記憶體＋localStorage）、JWT payload 解碼、登入流程錯誤映射。
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  clearToken,
  decodeSession,
  getToken,
  login,
  setToken,
} from "@/lib/auth";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) =>
    Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.signature`;
}

afterEach(() => {
  clearToken();
  vi.restoreAllMocks();
});

describe("token 儲存", () => {
  it("set 後可 get，並落 localStorage（永不過期裁示：跨重開保留）", () => {
    setToken("tok-123");
    expect(getToken()).toBe("tok-123");
    expect(window.localStorage.getItem("lu-camp.access-token")).toBe("tok-123");
  });

  it("clear 後兩處皆空", () => {
    setToken("tok-123");
    clearToken();
    expect(getToken()).toBeNull();
    expect(window.localStorage.getItem("lu-camp.access-token")).toBeNull();
  });

  it("記憶體空時從 localStorage 復原（頁面重新整理情境）", () => {
    window.localStorage.setItem("lu-camp.access-token", "persisted");
    expect(getToken()).toBe("persisted");
  });
});

describe("decodeSession", () => {
  it("解出 sub/role/store_id（僅供 UI 顯示；權威驗證在後端）", () => {
    setToken(fakeJwt({ sub: "7", role: "MANAGER", store_id: 1 }));
    expect(decodeSession()).toEqual({ userId: 7, role: "MANAGER", storeId: 1 });
  });

  it("無 token 或格式壞 → null（不丟例外）", () => {
    expect(decodeSession()).toBeNull();
    setToken("not-a-jwt");
    expect(decodeSession()).toBeNull();
  });
});

describe("login", () => {
  it("成功：存 token 並回 ok", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ access_token: "tok-ok", token_type: "bearer" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const result = await login("clerk1", "pw");
    expect(result.ok).toBe(true);
    expect(getToken()).toBe("tok-ok");
  });

  it("401：回後端訊息（帳號或密碼錯誤）、不存 token", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "帳號或密碼錯誤" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const result = await login("clerk1", "wrong");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toBe("帳號或密碼錯誤");
    expect(getToken()).toBeNull();
  });

  it("429：顯示節流訊息", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "嘗試次數過多，請稍後再試" }), {
          status: 429,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const result = await login("clerk1", "x");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toContain("嘗試次數過多");
  });

  it("網路錯誤：回通用訊息、不丟例外", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const result = await login("clerk1", "pw");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toContain("無法連線");
  });
});
