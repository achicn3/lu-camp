// F6 中文標籤（單一真實來源）：列舉 → zh-TW。專有名詞（品牌/型號/分類名稱）維持原樣。
// 以 Record<列舉, string> 確保列舉變動時 TS 編譯期強制補齊。
import type { components } from "@/lib/api-types";

type AcquisitionType = components["schemas"]["AcquisitionType"];
type PayoutMethod = components["schemas"]["PayoutMethod"];
type ContactRole = components["schemas"]["ContactRole"];
type Grade = components["schemas"]["Grade"];
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

// 成色文案（docs/10；可再定稿）。E 為散裝。
export const GRADE_LABEL: Record<Grade, string> = {
  S: "S 全新/近全新",
  A: "A 良好",
  B: "B 普通",
  C: "C 明顯使用",
  D: "D 瑕疵",
  E: "E 散裝",
};

export const BASIS_LABEL: Record<Basis, string> = {
  WEIGHT: "秤斤",
  BAG: "整袋",
  UNSPECIFIED: "未指定",
};

// 鑑價列可選的成色（S–D；E 走散裝）。
export const SERIALIZED_GRADES: Grade[] = ["S", "A", "B", "C", "D"];
