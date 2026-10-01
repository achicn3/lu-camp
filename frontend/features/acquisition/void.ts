// F6.5 作廢收購（void）前端純邏輯：可作廢預檢與錯誤訊息對應（單一真實來源、可單測）。
// 後端為最終權威（限 MANAGER、對稱反轉、各種衝突回 409/422）；此處僅做 UX 預檢與訊息呈現。
import { BUYOUT_ITEM_VOID_HINT, VOID_BLOCK_LABEL } from "@/features/acquisition/labels";
import type { components } from "@/lib/api-types";

type AcquisitionRead = components["schemas"]["AcquisitionRead"];
type VoidableFields = Pick<AcquisitionRead, "voided_at" | "type">;
type RecordRow = Pick<
  components["schemas"]["AcquisitionListItem"],
  "type" | "voided_at" | "void_block"
>;
type VoidBlock = NonNullable<RecordRow["void_block"]>;

/**
 * 收購紀錄上那一顆「作廢」鈕按下去做什麼（店主 2026-10-02：不再另分「選品作廢」）。
 * - SELECT_ALL：買斷單，開商品勾選視窗並預設全勾（＝整張作廢，取消勾選的保留）。
 * - SELECT_SOME：買斷單，整張不行但只作廢幾件可能可以——開視窗但**不預設全勾**：
 *   已上架一部分（docs/42 §10-2 不能整批作廢，後端擋全勾）、購物金已被用掉（整張沖不回）。
 * - WHOLE：散裝單，整張作廢。
 * - null：不能作廢（鈕反灰）。後端作廢端點仍是最終權威。
 */
export type RecordVoidMode = "SELECT_ALL" | "SELECT_SOME" | "WHOLE" | null;

const SELECT_SOME_BLOCKS: ReadonlySet<VoidBlock> = new Set(["PARTIALLY_LISTED", "CREDIT_SPENT"]);

export function recordVoidMode(row: RecordRow): RecordVoidMode {
  const block = row.void_block;
  if (row.type === "BUYOUT") {
    if (row.voided_at !== null) return null;
    if (block === null || block === "HAS_SOLD_ITEMS") return "SELECT_ALL"; // 已售的本來就勾不了
    return SELECT_SOME_BLOCKS.has(block) ? "SELECT_SOME" : null;
  }
  return block === null ? "WHOLE" : null;
}

/** 作廢鈕旁的說明：買斷單講清楚還能怎麼作廢；已作廢的單狀態欄已寫明，不重複。 */
export function recordVoidHint(row: RecordRow): string | null {
  const block = row.void_block;
  if (block === null || block === "ALREADY_VOIDED") return null;
  const itemHints: Partial<Record<VoidBlock, string>> = BUYOUT_ITEM_VOID_HINT;
  const itemHint = row.type === "BUYOUT" ? itemHints[block] : undefined;
  return itemHint ?? VOID_BLOCK_LABEL[block];
}

/** 前端預檢：未作廢且非寄售才顯示作廢入口（has-sold／credit-spent 無法前端判定，交後端回 409）。 */
export function canVoid(acq: VoidableFields): boolean {
  return acq.voided_at === null && acq.type !== "CONSIGNMENT";
}

/** 不可作廢時的中文說明（對應後端 409 已作廢／422 寄售不支援）；可作廢回 null。 */
export function voidBlockReason(acq: VoidableFields): string | null {
  if (acq.voided_at !== null) return "此收購已作廢，不可重複作廢";
  if (acq.type === "CONSIGNMENT") return "寄售收購不支援作廢，請走寄售退貨／結算反轉流程";
  return null;
}

// 後端 detail 已是分案 zh-TW（每種 409/422 各有明確訊息），故優先顯示；缺漏時才依 status 退回。
// 以 HTTP status 當退路（穩定）而非比對 detail 字串（易碎）。
const FALLBACK_BY_STATUS: Record<number, string> = {
  403: "僅限管理者作廢收購",
  404: "找不到收購單（單號可能有誤）",
  409: "此收購目前狀態不可作廢（可能已作廢、含已售出庫存、購物金已使用，或尚未開帳）",
  422: "無法作廢（請確認收購類型與作廢原因）",
};

/** 作廢失敗訊息：優先採後端 detail，否則依 HTTP status 退回預設，再否則通用失敗。 */
export function voidErrorMessage(status: number, detail: string | null): string {
  if (detail !== null && detail.trim() !== "") return detail;
  return FALLBACK_BY_STATUS[status] ?? "作廢失敗，請稍後再試";
}

/** 從 OpenAPI client 的錯誤物件取出 `detail` 字串（FastAPI 慣例）；無則 null。 */
export function errorDetail(error: unknown): string | null {
  if (error !== null && typeof error === "object" && "detail" in error) {
    const d = (error as { detail: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return null;
}
