// @vitest-environment jsdom
// (authed) 守衛測試：有 token 渲染內容；無 token 導回 /login；401 廣播導回。
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: vi.fn() }),
}));

import AuthedLayout from "@/app/(authed)/layout";
import { UNAUTHORIZED_EVENT, clearToken, setToken } from "@/lib/token";

afterEach(() => {
  cleanup();
  clearToken();
  vi.clearAllMocks();
});

describe("(authed) layout", () => {
  it("有 token：渲染內容、不導向", async () => {
    setToken("tok");
    render(
      <AuthedLayout>
        <p>受保護內容</p>
      </AuthedLayout>,
    );
    expect(await screen.findByText("受保護內容")).toBeDefined();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("token 只在 localStorage（重新整理情境）：仍渲染內容、不誤導去登入", async () => {
    window.localStorage.setItem("lu-camp.access-token", "persisted");
    render(
      <AuthedLayout>
        <p>受保護內容</p>
      </AuthedLayout>,
    );
    expect(await screen.findByText("受保護內容")).toBeDefined();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("無 token：不渲染內容、導回 /login", async () => {
    render(
      <AuthedLayout>
        <p>受保護內容</p>
      </AuthedLayout>,
    );
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("受保護內容")).toBeNull();
  });

  it("收到 401 廣播：導回 /login", async () => {
    setToken("tok");
    render(
      <AuthedLayout>
        <p>受保護內容</p>
      </AuthedLayout>,
    );
    await screen.findByText("受保護內容");
    window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
  });
});
