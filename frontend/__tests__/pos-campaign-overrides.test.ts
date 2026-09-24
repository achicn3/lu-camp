import { describe, expect, it } from "vitest";

import { disabledCampaignsSignature } from "@/features/pos/campaignOverrides";

describe("disabledCampaignsSignature（冪等鍵簽章，Codex 對抗審）", () => {
  it("順序與原因不同仍是同一個簽章", () => {
    const a = disabledCampaignsSignature([
      { campaign_id: 3, reason: "客人不要" },
      { campaign_id: 1, reason: null },
    ]);
    const b = disabledCampaignsSignature([
      { campaign_id: 1, reason: "另外議價" },
      { campaign_id: 3, reason: null },
    ]);
    expect(a).toEqual({ disabled_campaigns: [1, 3] });
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });

  it("沒取消時不放欄位（簽章與加欄位前相同）", () => {
    expect(disabledCampaignsSignature([])).toEqual({});
  });

  it("重複的活動只算一次", () => {
    expect(
      disabledCampaignsSignature([
        { campaign_id: 2, reason: null },
        { campaign_id: 2, reason: "x" },
      ]),
    ).toEqual({ disabled_campaigns: [2] });
  });
});
