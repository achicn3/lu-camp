// F6 中文標籤（單一真實來源）：列舉 → zh-TW。專有名詞（品牌/型號/分類名稱）維持原樣。
// 以 Record<列舉, string> 確保列舉變動時 TS 編譯期強制補齊。
import type { components } from "@/lib/api-types";
export { GRADE_LABEL, SERIALIZED_GRADES } from "@/features/inventory/grades";

type AcquisitionType = components["schemas"]["AcquisitionType"];
type PayoutMethod = components["schemas"]["PayoutMethod"];
type ContactRole = components["schemas"]["ContactRole"];
type Basis = components["schemas"]["BulkAcquisitionBasis"];

export const ACQ_TYPE_LABEL: Record<AcquisitionType, string> = {
  BUYOUT: "買斷",
  CONSIGNMENT: "寄售",
  BULK_LOT: "散裝",
};

export const PAYOUT_LABEL: Record<PayoutMethod, string> = {
  CASH: "現金",
  STORE_CREDIT: "購物金",
  SPLIT: "混合",
};

export const ROLE_LABEL: Record<ContactRole, string> = {
  MEMBER: "會員",
  SELLER: "賣方",
  CONSIGNOR: "寄售人",
};

export const BASIS_LABEL: Record<Basis, string> = {
  WEIGHT: "秤斤",
  BAG: "整袋",
  UNSPECIFIED: "未指定",
};
