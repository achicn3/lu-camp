// 線上 LINE Pay 單的發票去向：客人填的統編或手機條碼照填的開，都沒填就印紙本。
// 2026-10-21 店主：測試環境 LINE Pay 不回傳綁定的載具，拿掉「LINE Pay 上已綁定」，也不再用 LINE Pay 帶回的載具。
// （D1 orders.linepay_carrier 欄位保留但不再寫入。）POS 拉單與客人訂單頁共用這條規則，兩邊才不會說不一樣。

export interface InvoiceColumns {
  invoice_carrier: string | null;
  invoice_tax_id: string | null;
}

export type InvoiceDestination =
  | { kind: "CARRIER"; carrier: string }
  | { kind: "TAX_ID"; tax_id: string }
  | { kind: "PAPER" };

export function invoiceDestination(row: InvoiceColumns): InvoiceDestination {
  if (row.invoice_tax_id !== null) return { kind: "TAX_ID", tax_id: row.invoice_tax_id };
  if (row.invoice_carrier !== null) return { kind: "CARRIER", carrier: row.invoice_carrier };
  return { kind: "PAPER" };
}
