// F6 中文標籤（單一真實來源）：列舉 → zh-TW。專有名詞（品牌/型號/分類名稱）維持原樣。
// 以 Record<列舉, string> 確保列舉變動時 TS 編譯期強制補齊。
import type { components } from "@/lib/api-types";
export { GRADE_LABEL, SERIALIZED_GRADES } from "@/features/inventory/grades";

type AcquisitionType = components["schemas"]["AcquisitionType"];
type PayoutMethod = components["schemas"]["PayoutMethod"];
type ContactRole = components["schemas"]["ContactRole"];
type Basis = components["schemas"]["BulkAcquisitionBasis"];
type VoidBlock = components["schemas"]["AcquisitionVoidBlock"];

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
};

export const BASIS_LABEL: Record<Basis, string> = {
  WEIGHT: "秤斤",
  BAG: "整袋",
  UNSPECIFIED: "未指定",
};

/** 收購紀錄清單上「這張現在不能作廢」的原因（後端 void_block 算好，這裡只負責講人話）。 */
export const VOID_BLOCK_LABEL: Record<VoidBlock, string> = {
  CONSIGNMENT: "寄售不能作廢，請走寄售退貨",
  ALREADY_VOIDED: "已作廢",
  HAS_SOLD_ITEMS: "已有商品賣出，不能作廢",
  PARTIALLY_LISTED: "已上架一部分，不能整張作廢",
  CREDIT_SPENT: "購物金已被用掉，不能作廢",
  NO_OPEN_CASH_SESSION: "要退回現金，請先開帳",
};
