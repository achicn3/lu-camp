// @vitest-environment jsdom
// /login 頁元件測試：欄位、登入成功導向、錯誤訊息呈現。
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
}));

import LoginPage from "@/app/login/page";
import { clearToken, getToken } from "@/lib/auth";

afterEach(() => {
  cleanup();
  clearToken();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("/login", () => {
  it("有帳號/密碼欄位與登入主行動", () => {
    render(<LoginPage />);
    expect(screen.getByLabelText("帳號")).toBeDefined();
    expect(screen.getByLabelText("密碼")).toBeDefined();
    expect(screen.getByRole("button", { name: "登入" })).toBeDefined();
  });

  it("登入成功 → 存 token 並導向首頁", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ access_token: "tok", token_type: "bearer" }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    render(<LoginPage />);
    await userEvent.type(screen.getByLabelText("帳號"), "clerk1");
    await userEvent.type(screen.getByLabelText("密碼"), "pw-123456");
    await userEvent.click(screen.getByRole("button", { name: "登入" }));
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/"));
    expect(getToken()).toBe("tok");
  });

  it("登入失敗 → 顯示後端錯誤訊息、不導向", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "帳號或密碼錯誤" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    render(<LoginPage />);
    await userEvent.type(screen.getByLabelText("帳號"), "clerk1");
    await userEvent.type(screen.getByLabelText("密碼"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "登入" }));
    expect(await screen.findByText("帳號或密碼錯誤")).toBeDefined();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("空欄位不可送出（required）", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    render(<LoginPage />);
    await userEvent.click(screen.getByRole("button", { name: "登入" }));
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
