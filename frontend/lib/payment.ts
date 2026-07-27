import type { components } from "@/lib/api-types";
import { formatNtd, parseNtd } from "@/lib/money";

type PaymentMethod = components["schemas"]["PaymentMethod"];
type TenderType = components["schemas"]["TenderType"];

const PAYMENT_LABELS: Record<PaymentMethod | TenderType, string> = {
  CASH: "現金",
  STORE_CREDIT: "購物金",
  LINE_PAY: "LINE Pay",
  TAIWAN_PAY: "台灣Pay",
  MIXED: "混合",
};

/** 顯示實際收款拆分；單一付款仍沿用付款方式名稱。 */
export function formatSalePaymentSummary(sale: {
  payment_method: PaymentMethod;
  tenders: ReadonlyArray<{ tender_type: TenderType; amount: string }>;
}): string {
  const paidTenders = sale.tenders.filter(
    (tender) => (parseNtd(tender.amount) ?? 0) > 0,
  );
  if (paidTenders.length <= 1) return PAYMENT_LABELS[sale.payment_method];
  return paidTenders
    .map(
      (tender) =>
        `${PAYMENT_LABELS[tender.tender_type]} $${formatNtd(parseNtd(tender.amount) ?? 0)}`,
    )
    .join("＋");
}
