// @vitest-environment jsdom
// /menu 餐飲菜單管理頁測試：清單渲染、建立、上下架切換、MANAGER 權限閘。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import MenuPage from "@/app/(authed)/menu/page";
import { clearToken, setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const ITEMS = [
  { id: 1, store_id: 1, name: "手沖-耶加", unit_price: "180", category: "咖啡", is_available: true, sort_order: 0 },
  { id: 2, store_id: 1, name: "季節限定", unit_price: "200", category: null, is_available: false, sort_order: 1 },
];

type Route = (url: string, method: string, body: string) => Response | null;

function stubFetch(route: Route) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      const body =
        input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
      // 角色以 DB 現值為準（menu 頁 gate 改用 useCurrentRole）：測試一律回 MANAGER。
      if (url.includes("/auth/me")) return json({ id: 1, role: "MANAGER", store_id: 1 });
      const resp = route(url, method, body);
      if (resp) return resp;
      throw new Error(`unmatched fetch: ${method} ${url}`);
    }),
  );
}

function renderPage(role: "MANAGER" | "CLERK" = "MANAGER") {
  setToken(fakeJwt({ sub: "1", role, store_id: 1 }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<MenuPage />, { wrapper: Wrapper });
}

afterEach(() => {
  cleanup();
  clearToken();
  vi.unstubAllGlobals();
});

describe("/menu 餐飲菜單管理頁", () => {
  it("CLERK 無權限：顯示需管理者權限", () => {
    stubFetch(() => json([]));
    renderPage("CLERK");
    expect(screen.getByText("需管理者權限")).toBeTruthy();
  });

  it("MANAGER：清單渲染品名/售價/狀態（含停售）", async () => {
    stubFetch((url) => (url.includes("/menu-items") ? json(ITEMS) : null));
    renderPage("MANAGER");
    expect(await screen.findByText("手沖-耶加")).toBeTruthy();
    expect(screen.getByText("可售", { selector: ".inv-badge" })).toBeTruthy();
    expect(screen.getByText("停售", { selector: ".inv-badge" })).toBeTruthy();
  });

  it("建立品項：POST 後刷新清單", async () => {
    let posted = "";
    stubFetch((url, method, body) => {
      if (url.includes("/menu-items") && method === "POST") {
        posted = body;
        return json({ id: 9, store_id: 1, name: "拿鐵", unit_price: "150", category: "咖啡", is_available: true, sort_order: 0 }, 201);
      }
      if (url.includes("/menu-items")) return json(ITEMS);
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    await screen.findByText("手沖-耶加");
    await user.type(screen.getByLabelText("品名"), "拿鐵");
    await user.type(screen.getByLabelText("售價（整數元）"), "150");
    await user.click(screen.getByRole("button", { name: "新增品項" }));
    await waitFor(() => expect(posted).toContain("拿鐵"));
    expect(JSON.parse(posted).unit_price).toBe("150");
  });

  it("下架：PATCH is_available=false", async () => {
    let patched = "";
    stubFetch((url, method, body) => {
      if (url.includes("/menu-items/1") && method === "PATCH") {
        patched = body;
        return json({ ...ITEMS[0], is_available: false });
      }
      if (url.includes("/menu-items")) return json(ITEMS);
      return null;
    });
    const user = userEvent.setup();
    renderPage("MANAGER");
    await screen.findByText("手沖-耶加");
    // 第一列（可售）的「下架」鈕
    await user.click(screen.getAllByRole("button", { name: "下架" })[0]);
    await waitFor(() => expect(patched).toContain("is_available"));
    expect(JSON.parse(patched).is_available).toBe(false);
  });
});
