// @vitest-environment jsdom
// 估價詳細的商品簡稱建議（店主 2026-10-14）：打過的簡稱列出來可點選帶入；兩筆以上才列品牌；
// 每筆附上次估的原價／預計售價／收購價，點選後留在欄位下方對照。
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LineForm } from "@/features/intake/LineForm";
import {
  type ShortNameSuggestion,
  estimateParts,
} from "@/features/intake/ShortNameInput";
import { pricingRates } from "@/features/intake/estimate";

const RATES = pricingRates({
  default_margin_pct: 45,
  tax_rate: "0.0500",
  linepay_fee_pct: "0",
  taiwanpay_fee_pct: "0",
});

function suggestion(
  overrides: Partial<ShortNameSuggestion> = {},
): ShortNameSuggestion {
  return {
    short_name: "黑色折疊椅",
    brand_id: 1,
    brand_name: "Coleman",
    product_model_id: null,
    product_model_name: null,
    times: 2,
    estimated_on: "2026-10-03",
    acquisition_type: "BUYOUT",
    reference_price: "1000",
    discount_pct: 50,
    expected_listed_price: "500",
    deal_cost: "250",
    bulk_piece_count: null,
    ...overrides,
  };
}

afterEach(cleanup);

const MENU = "以前估過的商品";
const menuOptions = async () =>
  within(await screen.findByRole("listbox", { name: MENU })).getAllByRole(
    "option",
  );
const noMenu = () =>
  expect(screen.queryByRole("listbox", { name: MENU })).toBeNull();

function renderForm(search: (q: string) => Promise<ShortNameSuggestion[]>) {
  const onSubmit = vi.fn();
  render(
    <LineForm
      rates={RATES}
      defaultCommissionPct={50}
      submitLabel="存這一件"
      busy={false}
      onSubmit={onSubmit}
      searchShortNames={search}
    />,
  );
  return onSubmit;
}

describe("商品簡稱建議", () => {
  it("打字後列出以前估過的簡稱與價格，點選帶入簡稱、價格留在下方", async () => {
    const search = vi.fn().mockResolvedValue([suggestion()]);
    const onSubmit = renderForm(search);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("商品簡稱"), "折疊");

    const [option] = await menuOptions();
    expect(search).toHaveBeenLastCalledWith("折疊");
    expect(option.textContent).toContain("黑色折疊椅");
    expect(option.textContent).toContain("原價$1,000");
    expect(option.textContent).toContain("預計售價$500");
    expect(option.textContent).toContain("收購價$250");
    expect(option.textContent).toContain("10/3 · 估過 2 次");
    // 只有一筆：不必靠品牌分辨
    expect(option.textContent).not.toContain("Coleman");

    await user.click(option);
    expect((screen.getByLabelText("商品簡稱") as HTMLInputElement).value).toBe(
      "黑色折疊椅",
    );
    noMenu();
    const last = screen.getByLabelText("上次估價");
    expect(last.textContent).toContain("收購價$250");
    // 只帶入簡稱，價格不自動填
    expect((screen.getByLabelText("原價／件") as HTMLInputElement).value).toBe(
      "",
    );

    await user.click(screen.getByRole("button", { name: "存這一件" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ short_name: "黑色折疊椅" }),
    );
  });

  it("兩筆以上就列品牌；沒品牌的寫「沒填品牌」", async () => {
    const search = vi
      .fn()
      .mockResolvedValue([
        suggestion(),
        suggestion({ brand_id: 2, brand_name: "Snow Peak", deal_cost: "300" }),
        suggestion({ brand_id: null, brand_name: null }),
      ]);
    renderForm(search);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("商品簡稱"), "折疊");

    const options = await menuOptions();
    expect(
      options.map(
        (o) => within(o).queryByText(/Coleman|Snow Peak|沒填品牌/)?.textContent,
      ),
    ).toEqual(["Coleman", "Snow Peak", "沒填品牌"]);
  });

  it("方向鍵選、Enter 帶入，不會送出表單", async () => {
    const search = vi.fn().mockResolvedValue([
      suggestion(),
      suggestion({
        short_name: "黑色折疊桌",
        brand_id: null,
        brand_name: null,
      }),
    ]);
    const onSubmit = renderForm(search);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("商品簡稱"), "黑色");
    await menuOptions();
    await user.keyboard("{ArrowDown}{ArrowDown}{Enter}");

    expect((screen.getByLabelText("商品簡稱") as HTMLInputElement).value).toBe(
      "黑色折疊桌",
    );
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("改掉帶入的簡稱，上次估價就收起來", async () => {
    const search = vi
      .fn()
      .mockResolvedValueOnce([suggestion()])
      .mockResolvedValue([]);
    renderForm(search);
    const user = userEvent.setup();
    const input = screen.getByLabelText("商品簡稱");
    await user.type(input, "折疊");
    await user.click((await menuOptions())[0]);
    expect(screen.getByLabelText("上次估價")).toBeTruthy();

    await user.type(input, "２");
    expect(screen.queryByLabelText("上次估價")).toBeNull();
  });

  it("查不到（或查詢失敗）就沒有清單，照常手打", async () => {
    const search = vi.fn().mockRejectedValue(new Error("offline"));
    renderForm(search);
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("商品簡稱"), "新東西");
    await new Promise((r) => setTimeout(r, 300));
    noMenu();
    expect((screen.getByLabelText("商品簡稱") as HTMLInputElement).value).toBe(
      "新東西",
    );
  });
});

describe("上架時選的型號（店主 2026-10-15）", () => {
  it("有型號就寫「上架為 品牌 型號」，只有一筆也一樣帶品牌", async () => {
    renderForm(
      vi.fn().mockResolvedValue([
        suggestion({ brand_name: "Snow Peak", product_model_id: 9, product_model_name: "FD 椅" }),
      ]),
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("商品簡稱"), "FD");
    const [option] = await menuOptions();
    expect(option.textContent).toContain("上架為 Snow Peak FD 椅");
    await user.click(option);
    expect(screen.getByLabelText("上次估價").textContent).toContain("上架為 Snow Peak FD 椅");
  });

  it("帶入時連型號一起帶", async () => {
    const onSubmit = renderForm(
      vi.fn().mockResolvedValue([
        suggestion({ brand_name: "Snow Peak", product_model_id: 9, product_model_name: "FD 椅" }),
      ]),
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("商品簡稱"), "FD");
    await user.click((await menuOptions())[0]);
    await user.click(screen.getByRole("button", { name: "帶入" }));
    await user.click(screen.getByRole("button", { name: "存這一件" }));
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ brand_id: 1, product_model_id: 9 }));
  });
});

describe("帶入上次的內容（店主 2026-10-15）", () => {
  it("按「帶入」填上原價、折數、預計售價、收購價，送出時連品牌一起帶", async () => {
    const onSubmit = renderForm(vi.fn().mockResolvedValue([suggestion()]));
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("商品簡稱"), "折疊");
    await user.click((await menuOptions())[0]);
    await user.click(screen.getByRole("button", { name: "帶入" }));

    expect((screen.getByLabelText("原價／件") as HTMLInputElement).value).toBe(
      "1000",
    );
    expect(
      screen.getByRole("button", { name: "5折" }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      (screen.getByLabelText("預計售價／件") as HTMLInputElement).value,
    ).toBe("500");
    expect(
      (screen.getByLabelText("成交收購價／件") as HTMLInputElement).value,
    ).toBe("250");

    await user.click(screen.getByRole("button", { name: "存這一件" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        short_name: "黑色折疊椅",
        reference_price: "1000",
        discount_pct: 50,
        expected_listed_price: "500",
        deal_cost: "250",
        brand_id: 1,
      }),
    );
  });

  it("上次類型不同（散裝）就只帶原價，收購價不帶", async () => {
    renderForm(
      vi
        .fn()
        .mockResolvedValue([
          suggestion({
            acquisition_type: "BULK_LOT",
            deal_cost: "50",
            bulk_piece_count: 10,
          }),
        ]),
    );
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("商品簡稱"), "折疊");
    await user.click((await menuOptions())[0]);
    await user.click(screen.getByRole("button", { name: "帶入" }));

    expect((screen.getByLabelText("原價／件") as HTMLInputElement).value).toBe(
      "1000",
    );
    expect(
      (screen.getByLabelText("成交收購價／件") as HTMLInputElement).value,
    ).not.toBe("50");
    expect(screen.getByText(/上次是散裝，只帶入原價/)).toBeTruthy();
  });

  it("帶入後改掉簡稱，就不送品牌", async () => {
    const onSubmit = renderForm(
      vi.fn().mockResolvedValueOnce([suggestion()]).mockResolvedValue([]),
    );
    const user = userEvent.setup();
    const input = screen.getByLabelText("商品簡稱");
    await user.type(input, "折疊");
    await user.click((await menuOptions())[0]);
    await user.click(screen.getByRole("button", { name: "帶入" }));
    await user.type(input, "２");
    await user.click(screen.getByRole("button", { name: "存這一件" }));
    expect(onSubmit.mock.calls[0][0].brand_id).toBeNull();
  });
});

describe("上次估價的說法", () => {
  it("散裝是整堆收購價（附件數）、每件售價", () => {
    const parts = estimateParts(
      suggestion({
        acquisition_type: "BULK_LOT",
        reference_price: null,
        expected_listed_price: "20",
        deal_cost: "50",
        bulk_piece_count: 10,
      }),
    );
    expect(parts).toEqual([
      { label: "每件售價", value: "$20" },
      { label: "收購價", value: "整堆 $50（10 件）" },
    ]);
  });

  it("寄售只有寄售售價、沒有收購價", () => {
    const parts = estimateParts(
      suggestion({
        acquisition_type: "CONSIGNMENT",
        expected_listed_price: "800",
        deal_cost: null,
      }),
    );
    expect(parts).toEqual([
      { label: "原價", value: "$1,000" },
      { label: "寄售售價", value: "$800" },
    ]);
  });
});
