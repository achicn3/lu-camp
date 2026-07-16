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
  // 購物金溢價快照（簽署當下凍結的撥款條款：客人看到並簽的內容，必須完整呈現）
  const premium = content["store_credit_premium"];
  if (premium && typeof premium === "object") {
    const p = premium as { rate?: unknown; amount?: unknown; extra?: unknown };
    const pct =
      typeof p.rate === "string" || typeof p.rate === "number"
        ? `${(Number(p.rate) * 100).toFixed(1)}%`
        : "—";
    const amount = p.amount != null ? `$${String(p.amount)}` : "—";
    const extra = p.extra != null ? `（多得 $${String(p.extra)}）` : "";
    rows.push({
      label: "購物金溢價（凍結）",
      value: `${pct}，選購物金實發 ${amount}${extra}`,
    });
  }
  for (const [key, value] of Object.entries(content)) {
    if (key === "items" || key === "lot" || key === "store_credit_premium") continue;
    if (value === undefined) continue;
    // null 是「簽署當下顯示為 —」的合法值（如住址可空），不可丟棄——否則無法區分
    // 「空值」與「未投影」，證據失真（Codex 第三輪 P2）。未知巢狀值以 JSON 如實呈現。
    const text =
      value === null ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);
    rows.push({ label: KNOWN_FIELD_LABELS[key] ?? key, value: text });
  }
  return rows;
}

/** 綁定單據的顯示文字。切結/扣抵確認的綁定記在對方單據（signature_task_id），
 *  由 detail 端點反查回填 bound_*；ACK 則於建立時就指向銷售（ref_id）。 */
export function refLabel(
  kind: string,
  refType: string | null,
  refId: number | null,
  boundAcquisitionId?: number | null,
  boundSaleId?: number | null,
): string | null {
  if (kind === "ACQUISITION_AFFIDAVIT" && boundAcquisitionId != null) {
    return `收購單 #${boundAcquisitionId}`;
  }
  if (kind === "STORE_CREDIT_USE" && boundSaleId != null) {
    return `銷售單 #${boundSaleId}`;
  }
  if (kind === "TRANSACTION_ACK" && refType === "sale" && refId != null) {
    return `銷售單 #${refId}`;
  }
  return null;
}
