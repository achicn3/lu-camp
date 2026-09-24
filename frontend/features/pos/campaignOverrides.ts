// 「這筆不套用」的門市活動（docs/40 P1c）在冪等鍵簽章裡的正規形：只取活動 id、去重、排序。
// 後端指紋也只看排序後的 id（原因不影響金額）。前端若把原始陣列（含順序、原因）放進簽章，
// 回應遺失後重新取消、順序或原因不同就會換出新鍵——後端當成新交易，同一筆可能成交兩次。
import type { components } from "@/lib/api-types";

type DisabledCampaign = components["schemas"]["SaleCampaignOverrideRequest"];

/** 放進簽章的欄位；沒取消任何活動時回空物件（簽章維持加欄位前的形狀）。 */
export function disabledCampaignsSignature(
  disabled: DisabledCampaign[],
): { disabled_campaigns?: number[] } {
  if (disabled.length === 0) return {};
  const ids = [...new Set(disabled.map((d) => d.campaign_id))].sort((a, b) => a - b);
  return { disabled_campaigns: ids };
}
