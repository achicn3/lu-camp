// @vitest-environment jsdom
// /contacts 建檔表單防呆測試：姓名/電話必填、身分證字號檢核（不合法擋下、不送出 API）。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import ContactsPage from "@/app/(authed)/contacts/page";
import { setToken } from "@/lib/token";

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

type Route = (url: string, method: string, body: string) => Response | null;

function stubFetch(route: Route) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      const body =
        input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
      const resp = route(url, method, body);
      if (resp) return resp;
      throw new Error(`unmatched fetch: ${method} ${url}`);
    }),
  );
}

function renderPage() {
  setToken(fakeJwt({ sub: "1", role: "MANAGER", store_id: 1 }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<ContactsPage />, { wrapper: Wrapper });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("contacts 建檔防呆", () => {
  it("缺電話 → 擋下並提示，不打建檔 API", async () => {
    const posted: string[] = [];
    stubFetch((url, method, body) => {
      if (url.includes("/api/v1/contacts") && method === "POST") {
        posted.push(body);
        return json({}, 201);
      }
      if (url.includes("/api/v1/contacts")) return json([]); // 清單
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    const phone = screen.getByLabelText("電話 *") as HTMLInputElement;
    await user.type(screen.getByLabelText("姓名 *"), "王小明");
    await user.click(screen.getByRole("button", { name: "建檔" }));
    // 電話為必填（required）：空白時瀏覽器擋下送出，不會打建檔 API。
    expect(phone.validity.valueMissing).toBe(true);
    expect(posted).toHaveLength(0);
  });

  it("身分證字號不合法 → 擋下並提示，不打建檔 API", async () => {
    const posted: string[] = [];
    stubFetch((url, method, body) => {
      if (url.includes("/api/v1/contacts") && method === "POST") {
        posted.push(body);
        return json({}, 201);
      }
      if (url.includes("/api/v1/contacts")) return json([]);
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByLabelText("姓名 *"), "王小明");
    await user.type(screen.getByLabelText("電話 *"), "0912345678");
    await user.type(screen.getByLabelText("身分證字號（收購/寄售必填）"), "A123456788");
    await user.click(screen.getByRole("button", { name: "建檔" }));
    await waitFor(() =>
      expect(screen.getByText(/身分證字號格式或檢核碼不正確/)).toBeTruthy(),
    );
    expect(posted).toHaveLength(0);
  });

  it("姓名+電話齊全、身分證合法（或留空）→ 送出建檔", async () => {
    const posted: Record<string, unknown>[] = [];
    stubFetch((url, method, body) => {
      if (url.includes("/api/v1/contacts") && method === "POST") {
        posted.push(JSON.parse(body));
        return json({ id: 9 }, 201);
      }
      if (url.includes("/api/v1/contacts")) return json([]);
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByLabelText("姓名 *"), "王小明");
    await user.type(screen.getByLabelText("電話 *"), "0912345678");
    await user.type(screen.getByLabelText("身分證字號（收購/寄售必填）"), "A123456789");
    await user.click(screen.getByRole("button", { name: "建檔" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({
      name: "王小明",
      phone: "0912345678",
      national_id: "A123456789",
    });
  });
});

describe("contacts 查找 / 所有會員", () => {
  it("預設不直接列出會員（查找前無清單）", () => {
    stubFetch((url) => (url.includes("/api/v1/contacts") ? json([]) : null));
    renderPage();
    // 未搜尋前不應有「搜尋結果」清單；只有搜尋框與建檔卡。
    expect(screen.queryByText("搜尋結果")).toBeNull();
    expect(screen.getByRole("button", { name: "查找會員" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "所有會員" })).toBeTruthy();
  });

  it("身分證部分輸入 → 擋下並提示『精確查詢』，不打 lookup API", async () => {
    const lookups: string[] = [];
    stubFetch((url, method) => {
      if (url.includes("/contacts/lookup") && method === "POST") {
        lookups.push(url);
        return json(null);
      }
      if (url.includes("/api/v1/contacts")) return json([]);
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: "身分證字號（精確）" }));
    await user.type(screen.getByLabelText("身分證字號搜尋"), "A12345"); // 不完整
    await user.click(screen.getByRole("button", { name: "搜尋" }));
    await waitFor(() => expect(screen.getByText(/只能完整、精確查詢/)).toBeTruthy());
    expect(lookups).toHaveLength(0);
  });

  it("身分證完整且合法 → 以 lookup 精確查詢並顯示結果", async () => {
    let lookupBody: string | null = null;
    stubFetch((url, method, body) => {
      if (url.includes("/contacts/lookup") && method === "POST") {
        lookupBody = body;
        return json({ id: 7, name: "陳大寶", phone: "0922222222", roles: ["MEMBER"], member_points: 3 });
      }
      if (url.includes("/api/v1/contacts")) return json([]);
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: "身分證字號（精確）" }));
    await user.type(screen.getByLabelText("身分證字號搜尋"), "A123456789");
    await user.click(screen.getByRole("button", { name: "搜尋" }));
    await waitFor(() => expect(screen.getByText("陳大寶")).toBeTruthy());
    expect(JSON.parse(lookupBody as unknown as string)).toEqual({ national_id: "A123456789" });
  });

  it("所有會員分頁列出會員＋購物金餘額", async () => {
    stubFetch((url) => {
      if (url.includes("/contacts/members")) {
        return json([
          { id: 1, name: "林愛麗", phone: "0911111111", roles: ["MEMBER"], member_points: 5, has_national_id: false, store_credit_balance: "1100" },
        ]);
      }
      if (url.includes("/api/v1/contacts")) return json([]);
      return null;
    });
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByRole("button", { name: "所有會員" }));
    await waitFor(() => expect(screen.getByText("林愛麗")).toBeTruthy());
    expect(screen.getByText("1,100")).toBeTruthy();
  });
});
