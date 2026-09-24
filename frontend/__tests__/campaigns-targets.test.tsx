// @vitest-environment jsdom
// 門市活動 v2 管理頁（docs/40 P1b）：可疊加開關、指定商品範圍（包含／排除），細到品牌、型號、單件。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import CampaignsPage from "@/app/(authed)/campaigns/page";
import { clearToken, setToken } from "@/lib/token";

const BASE = {
  store_id: 1,
  discount_pct: 30,
  starts_at: "2026-06-20T00:00:00Z",
  ends_at: "2026-06-30T23:59:59Z",
  applies_owned_serialized: true,
  applies_owned_bulk: true,
  applies_catalog: false,
  applies_consignment: false,
  created_by: 1,
  created_at: "2026-06-19T10:00:00Z",
  updated_at: "2026-06-19T10:00:00Z",
};

const TARGETED = {
  ...BASE,
  id: 7,
  name: "Snow Peak 七折",
  status: "ACTIVE" as const,
  stackable: true,
  targets: [
    { mode: "INCLUDE", target_type: "BRAND", target_id: 5, label: "Snow Peak" },
    { mode: "INCLUDE", target_type: "PRODUCT_MODEL", target_id: 8, label: "Coleman 營燈 Lumiere" },
    { mode: "EXCLUDE", target_type: "SERIALIZED_ITEM", target_id: 9, label: "展示帳篷（ITM-9）" },
  ],
};

function fakeJwt(payload: Record<string, unknown>): string {
  const b64 = (obj: unknown) => Buffer.from(JSON.stringify(obj)).toString("base64url");
  return `${b64({ alg: "HS256" })}.${b64(payload)}.sig`;
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let posted: Record<string, unknown> | null = null;
let requested: string[] = [];
let releaseCode: () => void = () => {};

const MODELS: Record<number, { id: number; brand_id: number; name: string }[]> = {
  5: [
    { id: 8, brand_id: 5, name: "Amenity Dome" },
    { id: 81, brand_id: 5, name: "Land Lock" },
  ],
  6: [{ id: 90, brand_id: 6, name: "Lumiere" }],
};

function stub(list: unknown[] = [], { holdBarcode = false }: { holdBarcode?: boolean } = {}) {
  posted = null;
  requested = [];
  const gate = new Promise<void>((resolve) => {
    releaseCode = resolve;
  });
  if (!holdBarcode) releaseCode();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const req = input instanceof Request ? input : undefined;
      const url = req?.url ?? String(input);
      requested.push(url);
      if (url.includes("/api/v1/brands")) {
        const q = new URL(url).searchParams.get("q") ?? "";
        const brands = [
          { id: 5, store_id: 1, name: "Snow Peak" },
          { id: 6, store_id: 1, name: "Coleman" },
        ];
        return json(brands.filter((b) => b.name.toLowerCase().includes(q.toLowerCase())));
      }
      if (url.includes("/api/v1/product-models")) {
        const params = new URL(url).searchParams;
        const q = params.get("q") ?? "";
        const models = MODELS[Number(params.get("brand_id"))] ?? [];
        return json(models.filter((m) => m.name.toLowerCase().includes(q.toLowerCase())));
      }
      if (url.includes("/serialized-items/by-code/ITM-9")) {
        await gate;
        return json({ id: 9, item_code: "ITM-9", name: "展示帳篷", listed_price: "8000" });
      }
      if (url.includes("/campaigns") && req?.method === "POST") {
        posted = (await req.json()) as Record<string, unknown>;
        return json({ ...BASE, id: 11, name: "新活動", status: "DRAFT", stackable: true, targets: [] }, 201);
      }
      if (url.includes("/campaigns/count")) return json({ count: list.length });
      if (url.includes("/campaigns")) return json(list);
      return json([]);
    }),
  );
}

function renderPage() {
  setToken(fakeJwt({ sub: "1", role: "MANAGER", store_id: 1 }));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return render(<CampaignsPage />, { wrapper });
}

async function fillBasics(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("活動名稱"), "新活動");
  await user.type(screen.getByLabelText("折扣 %（1-99）"), "30");
  await user.type(screen.getByLabelText("開始時間"), "2026-06-20T00:00");
  await user.type(screen.getByLabelText("結束時間"), "2026-06-30T23:59");
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearToken();
});

describe("活動範圍與疊加", () => {
  it("可以指定品牌（包含）與單件商品（排除），並設定可疊加", async () => {
    stub();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("尚無活動");
    await fillBasics(user);

    await user.click(screen.getByLabelText("可以和其他活動疊加"));

    const scope = screen.getByRole("group", { name: "指定商品（選填）" });
    await user.selectOptions(within(scope).getByLabelText("範圍類型"), "BRAND");
    await user.type(within(scope).getByLabelText("搜尋品牌"), "Snow");
    await user.click(await within(scope).findByRole("button", { name: "加入 Snow Peak" }));

    await user.click(within(scope).getByLabelText("這些商品不套用"));
    await user.selectOptions(within(scope).getByLabelText("範圍類型"), "SERIALIZED_ITEM");
    await user.type(within(scope).getByLabelText("商品條碼"), "ITM-9");
    await user.click(within(scope).getByRole("button", { name: "加入這件" }));
    await within(scope).findByText("展示帳篷（ITM-9）");

    await user.click(screen.getByRole("button", { name: "建立活動" }));
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({
      stackable: true,
      targets: [
        { mode: "INCLUDE", target_type: "BRAND", target_id: 5 },
        { mode: "EXCLUDE", target_type: "SERIALIZED_ITEM", target_id: 9 },
      ],
    });
  });

  it("型號要先選品牌，加入後顯示「品牌 型號」；可移除", async () => {
    stub();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("尚無活動");
    await fillBasics(user);

    const scope = screen.getByRole("group", { name: "指定商品（選填）" });
    await user.selectOptions(within(scope).getByLabelText("範圍類型"), "PRODUCT_MODEL");
    await user.type(within(scope).getByLabelText("搜尋品牌"), "Snow");
    await user.click(await within(scope).findByRole("button", { name: "選 Snow Peak" }));
    await user.click(await within(scope).findByRole("button", { name: "加入 Snow Peak Amenity Dome" }));
    expect(within(scope).getByText("Snow Peak Amenity Dome")).toBeTruthy();

    await user.click(within(scope).getByRole("button", { name: "移除 Snow Peak Amenity Dome" }));
    expect(within(scope).queryByText("Snow Peak Amenity Dome")).toBeNull();

    await user.click(screen.getByRole("button", { name: "建立活動" }));
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({ stackable: false, targets: [] });
  });

  it("清單顯示可疊加與指定範圍", async () => {
    stub([TARGETED]);
    renderPage();
    const row = (await screen.findByText("Snow Peak 七折")).closest("tr") as HTMLElement;
    expect(row.textContent).toContain("可疊加");
    expect(row.textContent).toContain("只限：Snow Peak、Coleman 營燈 Lumiere");
    expect(row.textContent).toContain("排除：展示帳篷（ITM-9）");
  });

  it("說明多個活動同時進行時怎麼算", async () => {
    stub();
    renderPage();
    await screen.findByText("尚無活動");
    expect(screen.getByText(/不可疊加的活動不會跟其他活動一起用/)).toBeTruthy();
  });

  it("在搜尋框按 Enter 不會把整張活動送出（Codex 審查）", async () => {
    stub();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("尚無活動");
    await fillBasics(user);
    const scope = screen.getByRole("group", { name: "指定商品（選填）" });
    await user.type(within(scope).getByLabelText("搜尋品牌"), "Snow{Enter}");
    await within(scope).findByRole("button", { name: "加入 Snow Peak" });
    expect(posted).toBeNull();
  });

  it("同一個活動可以加多個型號：同品牌連續加、換品牌再加（型號也能搜尋）", async () => {
    stub();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("尚無活動");
    await fillBasics(user);
    const scope = screen.getByRole("group", { name: "指定商品（選填）" });
    await user.selectOptions(within(scope).getByLabelText("範圍類型"), "PRODUCT_MODEL");
    await user.type(within(scope).getByLabelText("搜尋品牌"), "Snow");
    await user.click(await within(scope).findByRole("button", { name: "選 Snow Peak" }));
    await user.click(await within(scope).findByRole("button", { name: "加入 Snow Peak Amenity Dome" }));
    // 留在同一個品牌，已加的不再列出，可以接著加
    expect(within(scope).queryByRole("button", { name: "加入 Snow Peak Amenity Dome" })).toBeNull();
    await user.type(within(scope).getByLabelText("搜尋型號"), "Land");
    await user.click(await within(scope).findByRole("button", { name: "加入 Snow Peak Land Lock" }));
    expect(requested.some((u) => u.includes("product-models") && u.includes("q=Land"))).toBe(true);

    await user.click(within(scope).getByRole("button", { name: "換品牌" }));
    await user.type(within(scope).getByLabelText("搜尋品牌"), "Cole");
    await user.click(await within(scope).findByRole("button", { name: "選 Coleman" }));
    await user.click(await within(scope).findByRole("button", { name: "加入 Coleman Lumiere" }));

    await user.click(screen.getByRole("button", { name: "建立活動" }));
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({
      targets: [
        { mode: "INCLUDE", target_type: "PRODUCT_MODEL", target_id: 8 },
        { mode: "INCLUDE", target_type: "PRODUCT_MODEL", target_id: 81 },
        { mode: "INCLUDE", target_type: "PRODUCT_MODEL", target_id: 90 },
      ],
    });
  });

  it("找不到時講清楚", async () => {
    stub();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("尚無活動");
    const scope = screen.getByRole("group", { name: "指定商品（選填）" });
    await user.type(within(scope).getByLabelText("搜尋品牌"), "不存在的牌子");
    expect(await within(scope).findByText("查無符合的品牌")).toBeTruthy();
  });

  it("條碼查詢還沒回來時加了別的範圍，兩個都要留著（Codex 審查）", async () => {
    stub([], { holdBarcode: true });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("尚無活動");
    const scope = screen.getByRole("group", { name: "指定商品（選填）" });
    await user.selectOptions(within(scope).getByLabelText("範圍類型"), "SERIALIZED_ITEM");
    await user.type(within(scope).getByLabelText("商品條碼"), "ITM-9");
    await user.click(within(scope).getByRole("button", { name: "加入這件" }));
    await user.selectOptions(within(scope).getByLabelText("範圍類型"), "BRAND");
    await user.type(within(scope).getByLabelText("搜尋品牌"), "Snow");
    await user.click(await within(scope).findByRole("button", { name: "加入 Snow Peak" }));
    releaseCode();
    await within(scope).findByText("展示帳篷（ITM-9）");
    expect(within(scope).getByText("Snow Peak")).toBeTruthy();
  });

  it("條碼查詢還沒回來前不能建立活動，免得少了範圍變成全館活動（Codex 審查）", async () => {
    stub([], { holdBarcode: true });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("尚無活動");
    await fillBasics(user);
    const scope = screen.getByRole("group", { name: "指定商品（選填）" });
    await user.selectOptions(within(scope).getByLabelText("範圍類型"), "SERIALIZED_ITEM");
    await user.type(within(scope).getByLabelText("商品條碼"), "ITM-9");
    await user.click(within(scope).getByRole("button", { name: "加入這件" }));

    const submit = screen.getByRole("button", { name: /建立活動|查詢商品中/ }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    releaseCode();
    await within(scope).findByText("展示帳篷（ITM-9）");
    await waitFor(() => expect(submit.disabled).toBe(false));
  });

  it("條碼查詢中不能再查第二個（避免重疊查詢提早解鎖送出）", async () => {
    stub([], { holdBarcode: true });
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("尚無活動");
    const scope = screen.getByRole("group", { name: "指定商品（選填）" });
    await user.selectOptions(within(scope).getByLabelText("範圍類型"), "SERIALIZED_ITEM");
    await user.type(within(scope).getByLabelText("商品條碼"), "ITM-9");
    await user.click(within(scope).getByRole("button", { name: "加入這件" }));
    const busy = within(scope).getByRole("button", { name: "查詢中…" }) as HTMLButtonElement;
    expect(busy.disabled).toBe(true);
    releaseCode();
    await within(scope).findByText("展示帳篷（ITM-9）");
  });

  it("條碼查詢連線失敗時講清楚、可以再試（Codex 審查）", async () => {
    stub();
    const original = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        if (url.includes("/serialized-items/by-code/")) throw new TypeError("network down");
        return original(input);
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("尚無活動");
    const scope = screen.getByRole("group", { name: "指定商品（選填）" });
    await user.selectOptions(within(scope).getByLabelText("範圍類型"), "SERIALIZED_ITEM");
    await user.type(within(scope).getByLabelText("商品條碼"), "ITM-9");
    await user.click(within(scope).getByRole("button", { name: "加入這件" }));
    expect((await within(scope).findByRole("alert")).textContent).toContain("查詢商品失敗");
    expect((within(scope).getByRole("button", { name: "加入這件" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("可以建立指定特價的活動（docs/40 P2）", async () => {
    stub();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("尚無活動");
    await user.type(screen.getByLabelText("活動名稱"), "營燈特價");
    await user.click(screen.getByLabelText("指定特價"));
    await user.type(screen.getByLabelText("特價（含稅，元）"), "690");
    await user.type(screen.getByLabelText("開始時間"), "2026-06-20T00:00");
    await user.type(screen.getByLabelText("結束時間"), "2026-06-30T23:59");
    await user.click(screen.getByRole("button", { name: "建立活動" }));
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({ kind: "FIXED_PRICE", fixed_price: "690" });
    expect(posted && "discount_pct" in posted).toBe(false);
  });

  it("清單以白話顯示特價與折金額", async () => {
    stub([
      { ...TARGETED, id: 21, name: "營燈特價", kind: "FIXED_PRICE", discount_pct: null, fixed_price: "690", amount_off: null },
      { ...TARGETED, id: 22, name: "每件折百", kind: "AMOUNT_OFF", discount_pct: null, fixed_price: null, amount_off: "100" },
    ]);
    renderPage();
    const fixedRow = (await screen.findByText("營燈特價")).closest("tr") as HTMLElement;
    expect(fixedRow.textContent).toContain("特價 $690");
    const offRow = screen.getByText("每件折百").closest("tr") as HTMLElement;
    expect(offRow.textContent).toContain("每件折 $100");
  });
});

