// @vitest-environment jsdom
// 待整理上架頁的兩個小元件（店主 2026-10-09 回報）：
// - 成色下拉：還沒選成色時要顯示「請選成色」，選第一個（全新）也要真的選得到。
// - 客人不賣了（退回）：走收購選品作廢，送出後告訴店員要收回多少錢。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GradeSelect } from "@/features/intake/GradeSelect";
import { ReturnToCustomerAction } from "@/features/intake/ReturnToCustomerAction";
import type { components } from "@/lib/api-types";

type Item = components["schemas"]["IntakeItemRead"];

const chair: Item = {
  kind: "SERIALIZED",
  id: 449,
  code: "S1-A79DEA62E2",
  name: "15.1L 冒險系列 冰桶-軍綠",
  consignment: false,
  grade: null,
  brand_id: null,
  brand_name: null,
  product_model_id: null,
  product_model_name: null,
  category_id: 26,
  category_name: "冰桶",
  listed_price: "949",
  qty: 1,
  acquisition_cost: "450",
  retail_price: null,
  note: null,
  listed: false,
  missing: ["成色"],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("成色下拉", () => {
  it("還沒選成色：顯示「請選成色」，選全新（第一個）也會送出", async () => {
    const onChange = vi.fn();
    render(<GradeSelect label="S1-A 成色" value="" onChange={onChange} />);
    const select = screen.getByLabelText("S1-A 成色") as HTMLSelectElement;
    expect(select.value).toBe("");
    expect(select.selectedOptions[0]?.textContent).toBe("請選成色");
    await userEvent.setup().selectOptions(select, "N");
    expect(onChange).toHaveBeenCalledWith("N");
  });

  it("已有成色：照原本的成色顯示", () => {
    render(<GradeSelect label="S1-A 成色" value="B" onChange={() => {}} />);
    expect((screen.getByLabelText("S1-A 成色") as HTMLSelectElement).value).toBe("B");
  });
});

describe("客人不賣了（退回）", () => {
  it("確認後送出作廢，告訴店員要向客人收回的現金", async () => {
    let posted: { url: string; body: unknown } | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const request = input as Request;
        posted = { url: request.url, body: await request.clone().json() };
        return json({ item_id: 449, reversed_cash: "450", reversed_credit: "0" });
      }),
    );
    const onDone = vi.fn();
    const user = userEvent.setup();
    render(<ReturnToCustomerAction batchId={19} item={chair} onDone={onDone} />, { wrapper });

    await user.click(screen.getByRole("button", { name: "客人不賣了（退回）" }));
    await user.click(screen.getByRole("button", { name: "確定退回" }));

    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(posted).toEqual({
      url: expect.stringContaining("/api/v1/intake-batches/19/return-to-customer"),
      body: { kind: "SERIALIZED", id: 449, reason: "客人不賣了" },
    });
    expect(onDone).toHaveBeenCalledWith(
      "「15.1L 冒險系列 冰桶-軍綠」已退回客人：請向客人收回現金 $450，放進抽屜。",
    );
  });

  it("用購物金付的：說明已從客人的購物金扣回", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ item_id: 449, reversed_cash: "0", reversed_credit: "495" })),
    );
    const onDone = vi.fn();
    const user = userEvent.setup();
    render(<ReturnToCustomerAction batchId={19} item={chair} onDone={onDone} />, { wrapper });

    await user.click(screen.getByRole("button", { name: "客人不賣了（退回）" }));
    await user.click(screen.getByRole("button", { name: "確定退回" }));

    await waitFor(() =>
      expect(onDone).toHaveBeenCalledWith(
        "「15.1L 冒險系列 冰桶-軍綠」已退回客人：已從客人的購物金扣回 $495。",
      ),
    );
  });

  it("後端拒絕（例如沒開帳）：顯示原因、不結束", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ detail: "作廢付現收購需先開帳" }, 409)),
    );
    const onDone = vi.fn();
    const user = userEvent.setup();
    render(<ReturnToCustomerAction batchId={19} item={chair} onDone={onDone} />, { wrapper });

    await user.click(screen.getByRole("button", { name: "客人不賣了（退回）" }));
    await user.click(screen.getByRole("button", { name: "確定退回" }));

    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "作廢付現收購需先開帳",
    );
    expect(onDone).not.toHaveBeenCalled();
  });
});
