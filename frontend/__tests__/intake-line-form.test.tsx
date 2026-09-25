// @vitest-environment jsdom
// 收購佇列估價表單：折數自動帶預計售價與建議收購價（與收購頁同一套）、成色沒點依折數推斷、
// 店員改過成交價就不再被覆蓋、六折紅字、欄位驗證。
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LineForm } from "@/features/intake/LineForm";
import { pricingRates } from "@/features/intake/estimate";

const RATES = pricingRates({
  default_margin_pct: 45,
  tax_rate: "0.0500",
  linepay_fee_pct: "0.0220",
  taiwanpay_fee_pct: "0",
});

afterEach(cleanup);

function renderForm() {
  const onSubmit = vi.fn();
  render(
    <LineForm rates={RATES} defaultCommissionPct={50} submitLabel="存這一件" busy={false} onSubmit={onSubmit} />,
  );
  return onSubmit;
}

describe("收購佇列估價表單", () => {
  it("五折帶預計售價 500、建議收購價 256，送出時成色依折數推斷為 A", async () => {
    const onSubmit = renderForm();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("商品簡稱"), "黑色折疊椅");
    await user.type(screen.getByLabelText("原價／件"), "1000");
    await user.click(screen.getByRole("button", { name: "5折" }));
    expect((screen.getByLabelText("預計售價／件") as HTMLInputElement).value).toBe("500");
    expect((screen.getByLabelText("成交收購價／件") as HTMLInputElement).value).toBe("256");
    await user.click(screen.getByRole("button", { name: "存這一件" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        short_name: "黑色折疊椅",
        qty: 1,
        acquisition_type: "BUYOUT",
        reference_price: "1000",
        discount_pct: 50,
        expected_listed_price: "500",
        suggested_cost: "256",
        deal_cost: "256",
        grade: "A",
        commission_pct: null,
      }),
    );
  });

  it("店員改過成交價，再換折數也不會被建議價蓋掉", async () => {
    renderForm();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("原價／件"), "1000");
    await user.click(screen.getByRole("button", { name: "5折" }));
    const deal = screen.getByLabelText("成交收購價／件") as HTMLInputElement;
    await user.clear(deal);
    await user.type(deal, "200");
    await user.click(screen.getByRole("button", { name: "4折" }));
    expect(deal.value).toBe("200");
    expect((screen.getByLabelText("預計售價／件") as HTMLInputElement).value).toBe("400");
  });

  it("六折以上紅字提醒確認成色", async () => {
    renderForm();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("原價／件"), "1000");
    await user.click(screen.getByRole("button", { name: "6折" }));
    expect(screen.getByRole("alert").textContent).toContain("可能是新品");
  });

  it("寄售送抽成、不送收購價；沒簡稱或折數沒原價擋下", async () => {
    const onSubmit = renderForm();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "存這一件" }));
    expect(screen.getByRole("alert").textContent).toContain("商品簡稱");
    await user.type(screen.getByLabelText("商品簡稱"), "帳篷");
    await user.click(screen.getByRole("button", { name: "5折" }));
    await user.click(screen.getByRole("button", { name: "存這一件" }));
    expect(screen.getByRole("alert").textContent).toContain("先填原價");
    await user.type(screen.getByLabelText("原價／件"), "8000");
    await user.selectOptions(screen.getByLabelText("類型"), "CONSIGNMENT");
    await user.click(screen.getByRole("button", { name: "存這一件" }));
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ acquisition_type: "CONSIGNMENT", commission_pct: 50, deal_cost: null }),
    );
  });
});
