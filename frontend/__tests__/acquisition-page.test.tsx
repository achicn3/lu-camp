// @vitest-environment jsdom
// /acquisition 頁元件測試（非 combobox 深互動部分）：中文分頁、賣方建檔、驗證閘、散裝表單。
// 完整買斷+定價輔助+送出流程由瀏覽器 E2E 覆蓋。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import AcquisitionPage from "@/app/(authed)/acquisition/page";

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SELLER = {
  id: 7,
  store_id: 1,
  name: "王賣家",
  roles: ["SELLER"],
  national_id_masked: "A12****789",
  has_national_id: true,
};

function stub(over: { drawer?: boolean } = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      if (url.includes("/categories") && method === "GET") {
        return json([{ id: 1, name: "登山服飾", target_margin_pct: 45 }]);
      }
      if (url.includes("/settings")) {
        return json({ premium_rate: "0.1000", default_margin_pct: 45 });
      }
      if (url.includes("/cash-sessions/current")) {
        return over.drawer === false ? json(null, 404) : json({ id: 1, status: "OPEN" });
      }
      if (url.includes("/contacts") && method === "POST") return json(SELLER, 201);
      if (url.includes("/contacts") && method === "GET") return json([]);
      return json([]);
    }),
  );
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<AcquisitionPage />, { wrapper });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AcquisitionPage", () => {
  it("renders zh-TW type tabs", () => {
    stub();
    renderPage();
    expect(screen.getByRole("tab", { name: "買斷" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "寄售" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "散裝" })).toBeTruthy();
  });

  it("bulk tab shows lot form with zh-TW basis options", async () => {
    stub();
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "散裝" }));
    expect(await screen.findByText("散裝批")).toBeTruthy();
    expect(screen.getByText("秤斤")).toBeTruthy();
    expect(screen.getByText("整袋")).toBeTruthy();
  });

  it("creates a seller (姓名+手機+身分證) and shows it selected", async () => {
    stub();
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /建立新賣方/ }));
    await userEvent.type(screen.getByLabelText("姓名"), "王賣家");
    await userEvent.type(screen.getByLabelText("手機"), "0912345678");
    await userEvent.type(screen.getByLabelText("身分證字號"), "A123456789");
    await userEvent.click(screen.getByRole("button", { name: "建立並選取" }));
    expect(await screen.findByText("王賣家")).toBeTruthy();
    expect(screen.getByRole("button", { name: "更換" })).toBeTruthy();
  });

  it("建新賣方缺手機 → 擋下、不送出建檔", async () => {
    const posts: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
        if (url.includes("/categories")) return json([]);
        if (url.includes("/settings")) return json({ premium_rate: "0.1000", default_margin_pct: 45 });
        if (url.includes("/cash-sessions/current")) return json({ id: 1, status: "OPEN" });
        if (url.includes("/contacts") && method === "POST") {
          posts.push(url);
          return json(SELLER, 201);
        }
        return json([]);
      }),
    );
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: /建立新賣方/ }));
    await userEvent.type(screen.getByLabelText("姓名"), "王賣家");
    await userEvent.type(screen.getByLabelText("身分證字號"), "A123456789");
    await userEvent.click(screen.getByRole("button", { name: "建立並選取" }));
    expect(await screen.findByText(/皆必填/)).toBeTruthy();
    expect(posts).toHaveLength(0);
  });

  it("選到無證號的既有會員 → 補登身分證字號（PATCH 加證號+角色）", async () => {
    const MEMBER = {
      id: 9,
      store_id: 1,
      name: "買斷會員",
      phone: "0987654321",
      roles: ["MEMBER"],
      national_id_masked: null,
      has_national_id: false,
    };
    const patches: { national_id?: string; roles?: string[] }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : String(input);
        const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
        const body =
          input instanceof Request ? await input.clone().text() : String(init?.body ?? "");
        if (url.includes("/categories")) return json([]);
        if (url.includes("/settings")) return json({ premium_rate: "0.1000", default_margin_pct: 45 });
        if (url.includes("/cash-sessions/current")) return json({ id: 1, status: "OPEN" });
        if (url.includes("/contacts") && method === "GET") return json([MEMBER]);
        if (url.includes("/contacts") && method === "PATCH") {
          patches.push(JSON.parse(body));
          return json({ ...MEMBER, roles: ["MEMBER", "SELLER"], has_national_id: true }, 200);
        }
        return json([]);
      }),
    );
    renderPage();
    await userEvent.type(screen.getByLabelText("賣方搜尋"), "買斷會員");
    await userEvent.click(await screen.findByRole("button", { name: /買斷會員/ }));
    // 已選取但無證號 → 出現補登欄
    const nidInput = await screen.findByLabelText("補登身分證字號");
    await userEvent.type(nidInput, "A123456789");
    await userEvent.click(screen.getByRole("button", { name: /補登並設為賣方/ }));
    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0]).toMatchObject({ national_id: "A123456789" });
    expect(patches[0].roles).toEqual(expect.arrayContaining(["MEMBER", "SELLER"]));
  });

  it("blocks submit with validation errors when nothing filled", async () => {
    stub();
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "送出收購" }));
    expect(await screen.findByText("請先選擇或建立賣方/寄售人")).toBeTruthy();
  });
});
