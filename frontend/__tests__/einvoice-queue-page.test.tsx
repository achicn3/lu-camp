// @vitest-environment jsdom
// /einvoice-queue 分頁：佇列會一直累積（實機手冊庫已有一萬七千多筆待送出），
// 只抓最新 100 筆又不能翻頁的話，舊的——也就是卡最久、最該處理的那些——永遠看不到。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import EInvoiceQueuePage from "@/app/(authed)/einvoice-queue/page";
import { clearToken, setToken } from "@/lib/token";

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function item(id: number) {
  return {
    id,
    action: "ISSUE",
    status: "PENDING",
    attempts: 0,
    last_error: null,
    created_at: "2026-09-12T02:00:00Z",
    invoice_no: null,
    sale_id: id,
  };
}

/** 後端以 limit/offset 分頁；這裡照實回該頁的列與總筆數。 */
function stubQueue(total: number) {
  const calls: { offset: string | null; limit: string | null }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(input instanceof Request ? input.url : String(input));
      const limit = Number(url.searchParams.get("limit") ?? "50");
      const offset = Number(url.searchParams.get("offset") ?? "0");
      calls.push({
        limit: url.searchParams.get("limit"),
        offset: url.searchParams.get("offset"),
      });
      const ids = Array.from({ length: total }, (_, i) => total - i).slice(offset, offset + limit);
      return new Response(JSON.stringify({ items: ids.map(item), total }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return calls;
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<EInvoiceQueuePage />, { wrapper });
}

afterEach(() => {
  cleanup();
  clearToken();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("/einvoice-queue 分頁", () => {
  it("超過一頁時顯示總頁數，翻頁取的是下一段而不是重抓同一批", async () => {
    setToken(fakeJwt({ sub: "1", role: "MANAGER", store_id: 1 }));
    const calls = stubQueue(137);
    renderPage();

    expect(await screen.findByText(/第 1 \/ 3 頁・共 137 筆/)).toBeDefined();
    expect(calls[0].offset).toBe("0");

    await userEvent.click(screen.getByRole("button", { name: /下一頁/ }));
    await waitFor(() => expect(screen.getByText(/第 2 \/ 3 頁/)).toBeDefined());
    expect(calls.at(-1)?.offset).toBe(String(Number(calls[0].limit)));
    // 第二頁要真的換一批列（第一頁最新、第二頁接續）
    expect(screen.queryByText("#137")).toBeNull();
  });

  it("在最後一頁處理掉剩下的之後，說清楚是這頁空了並給路回去，不謊稱沒有待處理的", async () => {
    setToken(fakeJwt({ sub: "1", role: "MANAGER", store_id: 1 }));
    vi.spyOn(window, "confirm").mockReturnValue(true); // 開立要二次確認
    let total = 101; // 3 頁；最後一頁只有 1 筆
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
        if (method === "POST") {
          total -= 1; // 送出去就不在待送出清單裡了
          return new Response(JSON.stringify({ id: 1, status: "UPLOADED", last_error: null }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        const limit = Number(url.searchParams.get("limit") ?? "50");
        const offset = Number(url.searchParams.get("offset") ?? "0");
        const ids = Array.from({ length: total }, (_, i) => total - i).slice(offset, offset + limit);
        return new Response(JSON.stringify({ items: ids.map(item), total }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    renderPage();

    await screen.findByText(/第 1 \/ 3 頁/);
    await userEvent.click(screen.getByRole("button", { name: /下一頁/ }));
    await userEvent.click(screen.getByRole("button", { name: /下一頁/ }));
    await waitFor(() => expect(screen.getByText(/第 3 \/ 3 頁/)).toBeDefined());
    expect(screen.getAllByRole("row").length).toBe(2); // 表頭＋最後一筆

    await userEvent.click(screen.getAllByRole("button", { name: /立即送出第 \d+ 筆/ })[0]);

    // 不自動跳頁（重試之類的處置後那列還在清單裡，跳走會讓人按不到按鈕）；
    // 但也不能說「目前沒有需要處理的發票」——其他頁還有 100 筆。
    await waitFor(() => expect(screen.getByText(/這一頁已經沒有項目了/)).toBeDefined());
    expect(screen.queryByText(/目前沒有需要處理的發票/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "回第一頁" }));
    await waitFor(() => expect(screen.getByText(/第 1 \/ 2 頁・共 100 筆/)).toBeDefined());
  });

  it("換篩選頁籤時回到第一頁，不會停在舊頁碼查出空清單", async () => {
    setToken(fakeJwt({ sub: "1", role: "MANAGER", store_id: 1 }));
    const calls = stubQueue(137);
    renderPage();

    await screen.findByText(/第 1 \/ 3 頁/);
    await userEvent.click(screen.getByRole("button", { name: /下一頁/ }));
    await waitFor(() => expect(screen.getByText(/第 2 \/ 3 頁/)).toBeDefined());

    await userEvent.click(screen.getByRole("button", { name: "平台退回" }));
    await waitFor(() => expect(calls.at(-1)?.offset).toBe("0"));
  });
});
