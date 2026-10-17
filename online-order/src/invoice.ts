// 線上 LINE Pay 單的發票去向（店主 2026-10-17）：客人輸入的優先 → LINE Pay 綁定的載具 → 都沒有就印紙本。
// POS 拉單（照這個開發票）與客人訂單頁（付款後告訴客人）共用同一條規則，兩邊才不會說不一樣。

export interface InvoiceColumns {
  invoice_carrier: string | null;
  invoice_tax_id: string | null;
  linepay_carrier: string | null;
}

export type InvoiceDestination =
  | { kind: "CARRIER"; carrier: string }
  | { kind: "LINEPAY_CARRIER"; carrier: string }
  | { kind: "TAX_ID"; tax_id: string }
  | { kind: "PAPER" };

export function invoiceDestination(row: InvoiceColumns): InvoiceDestination {
  if (row.invoice_tax_id !== null) return { kind: "TAX_ID", tax_id: row.invoice_tax_id };
  if (row.invoice_carrier !== null) return { kind: "CARRIER", carrier: row.invoice_carrier };
  if (row.linepay_carrier !== null) return { kind: "LINEPAY_CARRIER", carrier: row.linepay_carrier };
  return { kind: "PAPER" };
}

/** POS 開發票用的欄位（統編、載具至多一個；都 null＝紙本）。 */
export function invoiceForPos(row: InvoiceColumns): { carrier: string | null; tax_id: string | null } {
  const to = invoiceDestination(row);
  if (to.kind === "TAX_ID") return { carrier: null, tax_id: to.tax_id };
  if (to.kind === "PAPER") return { carrier: null, tax_id: null };
  return { carrier: to.carrier, tax_id: null };
}
