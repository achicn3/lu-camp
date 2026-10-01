// @vitest-environment jsdom
// 菜單照片（docs/44 §3.4；O1d）：菜單頁上傳／換／移除照片；POS 磚顯示照片。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MenuPhotoCell } from "@/features/menu/MenuPhotoCell";
import { menuPhotoUrl } from "@/features/menu/menuPhoto";

const SHA = "a".repeat(64);

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

type Call = { url: string; method: string; body: BodyInit | null | undefined; request?: Request };

function stubFetch(route: (url: string, method: string) => Response): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      calls.push({ url, method, body: init?.body, request: input instanceof Request ? input : undefined });
      return route(url, method);
    }),
  );
  return calls;
}

function wrap(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

const ITEM = { id: 5, name: "戚風", photo_sha256: null as string | null };

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("menuPhotoUrl", () => {
  it("指向公開照片端點", () => {
    expect(menuPhotoUrl(SHA)).toMatch(new RegExp(`/api/v1/menu-photos/${SHA}\\.webp$`));
  });
});

describe("菜單照片欄", () => {
  it("有照片顯示縮圖與「換照片」；沒照片顯示「上傳照片」", () => {
    stubFetch(() => json({}));
    const { rerender } = wrap(<MenuPhotoCell item={ITEM} onChanged={() => {}} />);
    expect(screen.getByText("上傳照片")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <MenuPhotoCell item={{ ...ITEM, photo_sha256: SHA }} onChanged={() => {}} />
      </QueryClientProvider>,
    );
    expect(screen.getByRole("img", { name: "戚風 照片" }).getAttribute("src")).toBe(
      menuPhotoUrl(SHA),
    );
    expect(screen.getByText("換照片")).toBeTruthy();
  });

  it("選檔就上傳（multipart 的 file 欄位），成功後通知重抓", async () => {
    const calls = stubFetch(() => json({ ...ITEM, photo_sha256: SHA }));
    const append = vi.spyOn(FormData.prototype, "append");
    const onChanged = vi.fn();
    const user = userEvent.setup();
    wrap(<MenuPhotoCell item={ITEM} onChanged={onChanged} />);
    const file = new File([new Uint8Array([1, 2, 3])], "cake.heic", { type: "" });
    await user.upload(screen.getByLabelText("戚風 上傳照片"), file);
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    const call = calls.find((c) => c.method === "POST");
    expect(call?.url).toMatch(/\/api\/v1\/menu-items\/5\/photo$/);
    // jsdom 的 FormData 交給 undici 的 Request 會序列化壞掉（測試環境限制），
    // 所以驗「我們把哪個檔放進 file 欄位」與 multipart 標頭；真實上傳由瀏覽器煙霧驗。
    expect(append).toHaveBeenCalledWith("file", file, "cake.heic");
    expect(call?.request?.headers.get("content-type")).toMatch(/^multipart\/form-data/);
  });

  it("後端擋下（格式不對）顯示原因", async () => {
    stubFetch(() => json({ detail: "只接受 JPEG、PNG、WebP、HEIC（手機拍的）照片" }, 422));
    const user = userEvent.setup();
    wrap(<MenuPhotoCell item={ITEM} onChanged={() => {}} />);
    await user.upload(
      screen.getByLabelText("戚風 上傳照片"),
      new File(["x"], "a.jpg", { type: "image/jpeg" }),
    );
    expect((await screen.findByRole("alert")).textContent).toContain("只接受 JPEG");
  });

  it("超過 10 MB 前端先擋、不送出", async () => {
    const calls = stubFetch(() => json({}));
    const user = userEvent.setup();
    wrap(<MenuPhotoCell item={ITEM} onChanged={() => {}} />);
    const big = new File([new Uint8Array(10 * 1024 * 1024 + 1)], "big.jpg", { type: "image/jpeg" });
    await user.upload(screen.getByLabelText("戚風 上傳照片"), big);
    expect((await screen.findByRole("alert")).textContent).toContain("10 MB");
    expect(calls.length).toBe(0);
  });

  it("移除照片打 DELETE", async () => {
    const calls = stubFetch(() => json({ ...ITEM }));
    const onChanged = vi.fn();
    const user = userEvent.setup();
    wrap(<MenuPhotoCell item={{ ...ITEM, photo_sha256: SHA }} onChanged={onChanged} />);
    await user.click(screen.getByRole("button", { name: "戚風 移除照片" }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(calls.find((c) => c.method === "DELETE")?.url).toMatch(/\/menu-items\/5\/photo$/);
  });
});
