// Pure helpers for /campaigns page and POS banner (presentation only, zero money math).
// discount_pct is "percentage off" (e.g. 10 = 10% off = 9 折).

import type { components } from "@/lib/api-types";

type CampaignStatus = components["schemas"]["CampaignStatus"];

/** Convert discount_pct (% off) to traditional zh-TW "X 折" display. */
export function discountDisplay(discountPct: number): string {
  const zhe = 100 - discountPct; // e.g. 10% off → 90 → "9 折", 15% off → 85 → "85 折"
  if (zhe % 10 === 0) {
    return `${zhe / 10} 折`;
  }
  return `${zhe} 折`;
}

const STATUS_LABELS: Record<CampaignStatus, string> = {
  DRAFT: "草稿",
  ACTIVE: "生效中",
  ENDED: "已結束",
  CANCELLED: "已作廢",
};

/** Return zh-TW label for campaign status. */
export function statusLabel(status: CampaignStatus): string {
  return STATUS_LABELS[status];
}

/** Summarise which item types the campaign applies to. */
export function scopeSummary(flags: {
  applies_owned_serialized: boolean;
  applies_owned_bulk: boolean;
  applies_catalog: boolean;
  applies_consignment: boolean;
}): string {
  const parts: string[] = [];
  if (flags.applies_owned_serialized) parts.push("自有序號");
  if (flags.applies_owned_bulk) parts.push("自有散裝");
  if (flags.applies_catalog) parts.push("一般商品");
  if (flags.applies_consignment) parts.push("寄售");
  return parts.length > 0 ? parts.join("、") : "-";
}
