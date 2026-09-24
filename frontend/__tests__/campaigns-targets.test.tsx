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

function stub(list: unknown[] = []) {
  posted = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const req = input instanceof Request ? input : undefined;
      const url = req?.url ?? String(input);
      if (url.includes("/api/v1/brands")) return json([{ id: 5, store_id: 1, name: "Snow Peak" }]);
      if (url.includes("/api/v1/product-models")) {
        return json([{ id: 8, store_id: 1, brand_id: 5, name: "Amenity Dome" }]);
      }
      if (url.includes("/serialized-items/by-code/ITM-9")) {
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
});
