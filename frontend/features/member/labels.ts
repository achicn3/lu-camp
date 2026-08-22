// 會員中心顯示用標籤（純函式；zh-TW）。後端值 → 顯示文字，集中一處便於測試與一致性。

export const ROLE_LABELS: Record<string, string> = {
  MEMBER: "會員",
  SELLER: "賣方",
  CONSIGNOR: "寄售人",
};

export const SOURCE_TYPE_LABELS: Record<string, string> = {
  BUYOUT: "買斷",
  CONSIGNMENT: "寄售",
};

export const KIND_LABELS: Record<string, string> = {
  SERIALIZED: "序號品",
  BULK_LOT: "散裝",
};

export const SETTLEMENT_STATUS_LABELS: Record<string, string> = {
  PENDING: "待結算",
  PAID: "已結算",
  CANCELLED: "已取消",
};

// 商品狀態。**序號品與散裝批的狀態值不同**，但同一個欄位會出現兩種來源
// （寄售分頁列序號品，帶來的商品分頁兩者都有），所以合成一張表。
export const ITEM_STATUS_LABELS: Record<string, string> = {
  IN_STOCK: "在庫",
  SOLD: "已售出",
  RETURNED_TO_CONSIGNOR: "已退回寄售人",
  WRITTEN_OFF: "已報廢",
  ON_SALE: "販售中",
  SOLD_OUT: "已售完",
};

export const PAYMENT_METHOD_LABELS: Record<string, string> = {
  CASH: "現金",
  STORE_CREDIT: "購物金",
  MIXED: "混合",
};

export const INVOICE_STATUS_LABELS: Record<string, string> = {
  NOT_ISSUED: "未開立",
  PENDING_ISSUE: "發票開立中",
  ISSUED: "已開立",
  PENDING_ALLOWANCE: "折讓開立中",
  PENDING_VOID: "作廢處理中",
  ALLOWANCE: "已折讓",
  VOID: "已作廢",
};

/** 以對照表翻譯；查無則原樣回傳（避免吞掉未知後端值）。 */
export function labelFor(map: Record<string, string>, value: string): string {
  return map[value] ?? value;
}

/** 角色陣列轉中文（保留順序、去重）。 */
export function rolesLabel(roles: readonly string[]): string {
  if (roles.length === 0) return "—";
  return roles.map((r) => labelFor(ROLE_LABELS, r)).join("、");
}

export interface MemberTab {
  key: "overview" | "purchases" | "consignments" | "sourced" | "edit";
  label: string;
}

/** 會員 360 詳情頁分頁定義（編輯頁恆顯示，內部依角色控管欄位）。 */
export const MEMBER_TABS: readonly MemberTab[] = [
  { key: "overview", label: "總覽" },
  { key: "purchases", label: "消費紀錄" },
  { key: "consignments", label: "寄售" },
  { key: "sourced", label: "帶來的商品" },
  { key: "edit", label: "編輯" },
];
