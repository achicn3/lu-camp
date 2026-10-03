// 組合包袋裝條碼（ADR-028）的純邏輯：從組合價格子整理出可以放進袋子的商品、要送出的內容。
// 袋裡內容是不是剛好湊成一組，由後端用結帳同一支計價引擎判斷；這裡只整理畫面與送出格式。
import type { components } from "@/lib/api-types";

type BundleSlot = components["schemas"]["BundleSlotRead"];
type TargetType = components["schemas"]["CampaignTargetType"];
type PackItemType = components["schemas"]["BundlePackItemType"];
export type PackItemInput = components["schemas"]["BundlePackItemInput"];

export interface PackCandidate {
  key: string;
  item_type: PackItemType;
  target_id: number;
  label: string;
}

/** 範圍條件指到「單一商品」的才直接列出來填件數；分類／品牌／型號要另外用條碼加。 */
const CONCRETE: Partial<Record<TargetType, PackItemType>> = {
  CATALOG_PRODUCT: "CATALOG",
  BULK_BASKET: "BULK_BASKET",
  SERIALIZED_ITEM: "SERIALIZED",
};

export function packCandidateKey(itemType: PackItemType, targetId: number): string {
  return `${itemType}:${targetId}`;
}

/** 格子裡可以直接放的商品（去重、照格子順序），以及依分類／品牌／型號指定、要掃條碼加的範圍名稱。 */
export function packCandidates(slots: readonly BundleSlot[]): {
  items: PackCandidate[];
  broadTargets: string[];
} {
  const items = new Map<string, PackCandidate>();
  const broad = new Set<string>();
  for (const slot of slots) {
    for (const target of slot.targets) {
      const itemType = CONCRETE[target.target_type];
      if (itemType === undefined) {
        broad.add(target.label);
        continue;
      }
      const key = packCandidateKey(itemType, target.target_id);
      if (!items.has(key)) {
        items.set(key, { key, item_type: itemType, target_id: target.target_id, label: target.label });
      }
    }
  }
  return { items: [...items.values()], broadTargets: [...broad] };
}

/** 一組共要幾件（各格件數加總）。 */
export function packUnitsRequired(slots: readonly BundleSlot[]): number {
  return slots.reduce((sum, slot) => sum + slot.qty, 0);
}

/** 填了件數（正整數）的才送出。 */
export function packItems(
  candidates: readonly PackCandidate[],
  qtyByKey: Readonly<Record<string, string>>,
): PackItemInput[] {
  return candidates.flatMap((candidate) => {
    const text = (qtyByKey[candidate.key] ?? "").trim();
    if (!/^\d+$/.test(text) || Number(text) <= 0) return [];
    return [{ item_type: candidate.item_type, target_id: candidate.target_id, qty: Number(text) }];
  });
}
