// F6.5 作廢收購（void）前端純邏輯：可作廢預檢與錯誤訊息對應（單一真實來源、可單測）。
// 後端為最終權威（限 MANAGER、對稱反轉、各種衝突回 409/422）；此處僅做 UX 預檢與訊息呈現。
import type { components } from "@/lib/api-types";

type AcquisitionRead = components["schemas"]["AcquisitionRead"];
type VoidableFields = Pick<AcquisitionRead, "voided_at" | "type">;

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
