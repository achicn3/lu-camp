// @vitest-environment jsdom
// POS「本筆套用的活動」面板：購物車鎖住（送簽署、付款處理中）時不能再改活動（Codex 審查）。
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CampaignPanel } from "@/features/pos/CampaignPanel";

const APPLIED = [{ campaign_id: 2, name: "會員九折", discount_amount: "90" }];

afterEach(cleanup);

describe("CampaignPanel", () => {
  it("確認框打開後購物車被鎖住：確認框收起，按鈕與 Enter 都不能套用", async () => {
    const onDisable = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(
      <CampaignPanel applied={APPLIED} disabled={[]} locked={false} onDisable={onDisable} onRestore={vi.fn()} />,
    );
    await user.click(screen.getByRole("button", { name: "這筆不套用" }));
    const input = screen.getByLabelText("不套用原因");
    await user.type(input, "客人不要");

    rerender(
      <CampaignPanel applied={APPLIED} disabled={[]} locked={true} onDisable={onDisable} onRestore={vi.fn()} />,
    );
    expect(screen.queryByRole("button", { name: "確定不套用" })).toBeNull();
    expect(screen.queryByLabelText("不套用原因")).toBeNull();
    expect(onDisable).not.toHaveBeenCalled();
  });

  it("沒鎖住時確認會帶原因（空白原因送 null）", async () => {
    const onDisable = vi.fn();
    const user = userEvent.setup();
    render(
      <CampaignPanel applied={APPLIED} disabled={[]} locked={false} onDisable={onDisable} onRestore={vi.fn()} />,
    );
    await user.click(screen.getByRole("button", { name: "這筆不套用" }));
    await user.type(screen.getByLabelText("不套用原因"), "   {Enter}");
    expect(onDisable).toHaveBeenCalledWith(2, null);
  });
});
