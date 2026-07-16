// 簽署紀錄調閱（docs/29 波次一）：類型/狀態中文標籤與內容快照的顯示列。
// 純函式（vitest 直測）；內容快照鍵值因任務類型而異，未知鍵以通用列呈現。

export const SIGNING_KIND_LABELS: Record<string, string> = {
  ACQUISITION_AFFIDAVIT: "收購切結",
  STORE_CREDIT_USE: "購物金扣抵確認",
  TRANSACTION_ACK: "交易紀錄簽收",
};

export const SIGNING_STATUS_LABELS: Record<string, string> = {
  PENDING: "待簽署",
  SIGNED: "已簽署",
  CANCELLED: "已作廢",
};

export const SIGNING_PAYOUT_LABELS: Record<string, string> = {
  CASH: "現金",
  STORE_CREDIT: "購物金",
};

const KNOWN_FIELD_LABELS: Record<string, string> = {
  seller_name: "簽署人",
  national_id_masked: "身分證（遮罩）",
  phone: "電話",
  address: "住址",
  total: "總額",
  debit: "本次折抵",
  sale_total: "消費合計",
  balance_before: "折抵前餘額",
  balance_after: "折抵後餘額",
  sale_ref: "銷售單號",
  purchased_at: "交易時間",
  store_credit_premium: "購物金溢價率（凍結）",
};

export interface ContentRow {
  label: string;
  value: string;
}

interface ContentItem {
  name?: unknown;
  amount?: unknown;
}

/** 內容快照 → 顯示列。items（切結品項）與 lot（散裝）另行結構化，其餘鍵值攤平。 */
export function contentRows(content: Record<string, unknown>): ContentRow[] {
  const rows: ContentRow[] = [];
  const items = content["items"];
  if (Array.isArray(items)) {
    for (const [i, raw] of items.entries()) {
      const it = raw as ContentItem;
      rows.push({
        label: `品項 ${i + 1}`,
        value: `${String(it.name ?? "—")}（$${String(it.amount ?? "—")}）`,
      });
    }
  }
  const lot = content["lot"];
  if (lot && typeof lot === "object") {
    const l = lot as { total_qty?: unknown; acquisition_basis?: unknown };
    rows.push({
      label: "散裝批",
      value: `數量 ${String(l.total_qty ?? "—")}（計價基準 ${String(l.acquisition_basis ?? "—")}）`,
    });
  }
  for (const [key, value] of Object.entries(content)) {
    if (key === "items" || key === "lot") continue;
    if (value === null || value === undefined || typeof value === "object") continue;
    rows.push({ label: KNOWN_FIELD_LABELS[key] ?? key, value: String(value) });
  }
  return rows;
}

/** 綁定單據的顯示文字（AFFIDAVIT→收購單、SCU→銷售單、ACK→ref 銷售單）。 */
export function refLabel(
  kind: string,
  refType: string | null,
  refId: number | null,
): string | null {
  if (kind === "TRANSACTION_ACK" && refType === "sale" && refId != null) {
    return `銷售單 #${refId}`;
  }
  return null;
}
