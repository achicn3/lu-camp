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

/** 讓測試可以延後或弄壞 /settings 的回應（稅率晚到／讀不到的路徑）。 */
let releaseSettings: (() => void) | null = null;

function stub(over: { drawer?: boolean; taxRate?: string; settingsFails?: boolean; holdSettings?: boolean } = {}) {
  releaseSettings = null;
  const gate = over.holdSettings
    ? new Promise<void>((resolve) => {
        releaseSettings = resolve;
      })
    : null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      if (url.includes("/categories") && method === "GET") {
        return json([{ id: 1, name: "登山服飾", target_margin_pct: 45 }]);
      }
      if (url.includes("/settings")) {
        if (gate) await gate;
        if (over.settingsFails) return json({ detail: "boom" }, 500);
        return json({
          premium_rate: "0.1000",
          default_margin_pct: 45,
          tax_rate: over.taxRate ?? "0.0500",
        });
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

  it("打完估計轉售價（未稅）→ 上架售價自動帶入含稅價", async () => {
    stub();
    renderPage();
    const resale = await screen.findByLabelText("估計轉售價", { selector: "input" });
    await userEvent.type(resale, "2010");
    await waitFor(() =>
      expect((screen.getByLabelText("上架售價（含稅）", { selector: "input" }) as HTMLInputElement).value).toBe("2111"),
    );
  });

  it("估計轉售價再改一次 → 上架售價一律跟著換（即使已手動改過）", async () => {
    stub();
    renderPage();
    const resale = await screen.findByLabelText("估計轉售價", { selector: "input" });
    await userEvent.type(resale, "2010");
    const listed = screen.getByLabelText("上架售價（含稅）", { selector: "input" }) as HTMLInputElement;
    await waitFor(() => expect(listed.value).toBe("2111"));
    // 店員手動改成別的價
    await userEvent.clear(listed);
    await userEvent.type(listed, "2500");
    expect(listed.value).toBe("2500");
    // 再動估計轉售價 → 覆蓋回含稅價（店主裁示：無論如何就是同步）
    await userEvent.clear(resale);
    await userEvent.type(resale, "1000");
    await waitFor(() => expect(listed.value).toBe("1050"));
  });

  it("稅率不是 5% 時也要正確換算（稅率取自 settings，不得寫死）", async () => {
    stub({ taxRate: "0.1000" });
    renderPage();
    const resale = await screen.findByLabelText("估計轉售價", { selector: "input" });
    await userEvent.type(resale, "2010");
    await waitFor(() =>
      expect(
        (screen.getByLabelText("上架售價（含稅）", { selector: "input" }) as HTMLInputElement).value,
      ).toBe("2211"),
    );
  });

  it("稅率設定晚到時，不得蓋掉店員已手打的上架售價", async () => {
    stub({ holdSettings: true });
    renderPage();
    const resale = await screen.findByLabelText("估計轉售價", { selector: "input" });
    await userEvent.type(resale, "2010");
    const listed = screen.getByLabelText("上架售價（含稅）", { selector: "input" }) as HTMLInputElement;
    expect(listed.value).toBe(""); // 還沒有稅率，不能亂猜
    await userEvent.type(listed, "2500");
    expect(listed.value).toBe("2500");
    // 先填收購價，這樣稅率一落地就會出現「毛利 N%」——用它當「設定真的回來了」的正訊號。
    // 原本等的是「紅字消失」，但載入中本來就沒有紅字，waitFor 第一個 tick 就通過，
    // 等於在設定尚未落地的瞬間斷言，什麼都沒驗到（第三輪 M-2）。
    await userEvent.type(screen.getByLabelText("收購價"), "1000");
    releaseSettings?.(); // 設定這時才回來
    expect(await screen.findByText(/毛利 /)).toBeTruthy();
    expect(listed.value).toBe("2500"); // 店員打的價格必須原封不動
  });

  it("稅率設定晚到、上架售價還空著時才自動補上", async () => {
    stub({ holdSettings: true });
    renderPage();
    const resale = await screen.findByLabelText("估計轉售價", { selector: "input" });
    await userEvent.type(resale, "2010");
    const listed = screen.getByLabelText("上架售價（含稅）", { selector: "input" }) as HTMLInputElement;
    expect(listed.value).toBe("");
    releaseSettings?.();
    await waitFor(() => expect(listed.value).toBe("2111"));
  });

  it("讀不到稅率設定時，明講不能自動換算（不可靜默）", async () => {
    stub({ settingsFails: true });
    renderPage();
    const resale = await screen.findByLabelText("估計轉售價", { selector: "input" });
    await userEvent.type(resale, "2010");
    expect(
      (screen.getByLabelText("上架售價（含稅）", { selector: "input" }) as HTMLInputElement).value,
    ).toBe("");
    expect(await screen.findByText(/讀不到稅率設定/)).toBeTruthy();
  });

  it("切到散裝分頁再切回買斷，店員手打的上架售價不得被改掉", async () => {
    // ItemRowCard 在切分頁時會 unmount；同步用的 ref 若歸零，remount 會被當成
    // 「店員剛動了估計轉售價」而覆蓋手打值（實測曾 1800 → 1050，少收 750）。
    stub();
    renderPage();
    const resale = await screen.findByLabelText("估計轉售價", { selector: "input" });
    await userEvent.type(resale, "1000");
    const listed = () =>
      screen.getByLabelText("上架售價（含稅）", { selector: "input" }) as HTMLInputElement;
    await waitFor(() => expect(listed().value).toBe("1050"));
    await userEvent.clear(listed());
    await userEvent.type(listed(), "1800");
    expect(listed().value).toBe("1800");

    await userEvent.click(screen.getByRole("tab", { name: "散裝" }));
    expect(await screen.findByText("散裝批")).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "買斷" }));

    await waitFor(() => expect(listed().value).toBe("1800"));
  });

  it("在寄售分頁手打上架售價後切到買斷再切回，價格不得被加稅", async () => {
    // 切分頁是導覽動作、不是定價決定。談定的架上價 2000 若因為點一下「買斷」就變 2100，
    // 客人多付 100、應付寄售人多 50，而且沒有任何提示（第三輪 M-1）。
    stub();
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "寄售" }));
    const resale = await screen.findByLabelText("估計轉售價", { selector: "input" });
    await userEvent.type(resale, "2000");
    const listed = () =>
      screen.getByLabelText("上架售價（含稅）", { selector: "input" }) as HTMLInputElement;
    expect(listed().value).toBe(""); // 寄售不自動加稅
    await userEvent.type(listed(), "2000"); // 與寄售人談定的架上價

    await userEvent.click(screen.getByRole("tab", { name: "買斷" }));
    await waitFor(() => expect(listed().value).toBe("2000"));
    await userEvent.click(screen.getByRole("tab", { name: "寄售" }));
    expect(listed().value).toBe("2000");
  });

  it("在寄售分頁填了估計轉售價、上架售價還空著 → 切回買斷要補算", async () => {
    stub();
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "寄售" }));
    const resale = await screen.findByLabelText("估計轉售價", { selector: "input" });
    await userEvent.type(resale, "2000");
    await userEvent.click(screen.getByRole("tab", { name: "買斷" }));
    await waitFor(() =>
      expect(
        (screen.getByLabelText("上架售價（含稅）", { selector: "input" }) as HTMLInputElement)
          .value,
      ).toBe("2100"),
    );
  });

  it("寄售分頁不自動加稅，也不出現「帶入含稅價格」快捷", async () => {
    stub();
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "寄售" }));
    const resale = await screen.findByLabelText("估計轉售價", { selector: "input" });
    await userEvent.type(resale, "2000");
    await waitFor(() => expect(screen.queryByText(/帶入含稅價格/)).toBeNull());
    expect(
      (screen.getByLabelText("上架售價（含稅）", { selector: "input" }) as HTMLInputElement).value,
    ).toBe("");
  });

  it("設定還在載入時，不得先喊「讀不到稅率設定」", async () => {
    stub({ holdSettings: true });
    renderPage();
    await screen.findByLabelText("估計轉售價", { selector: "input" });
    expect(screen.queryByText(/讀不到稅率設定/)).toBeNull();
    releaseSettings?.();
    await waitFor(() => expect(screen.queryByText(/讀不到稅率設定/)).toBeNull());
  });

  it("blocks submit with validation errors when nothing filled", async () => {
    stub();
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "送出收購" }));
    expect(await screen.findByText("請先選擇或建立賣方/寄售人")).toBeTruthy();
  });
});
