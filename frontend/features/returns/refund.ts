import { parseNtd } from "@/lib/money";

export type RefundTenderType = "CASH" | "STORE_CREDIT" | "LINE_PAY" | "TAIWAN_PAY";

export interface RefundTenderLike {
  tender_type: RefundTenderType;
  amount: string;
}

export interface RefundLeg {
  tender_type: RefundTenderType;
  amount: number;
}

const EXTERNAL = new Set<RefundTenderType>(["CASH", "LINE_PAY", "TAIWAN_PAY"]);

export function supportsRefund(tenders: RefundTenderLike[]): boolean {
  const types = new Set(tenders.map((tender) => tender.tender_type));
  if (types.size === 0 || [...types].some((type) => !EXTERNAL.has(type) && type !== "STORE_CREDIT")) {
    return false;
  }
  const external = [...types].filter((type) => type !== "STORE_CREDIT");
  if (types.has("STORE_CREDIT")) return external.length <= 1;
  return types.size === 1 && external.length === 1;
}

/** 與後端相同的累計差額規則：購物金優先，其餘只回原本唯一的付款方式。 */
export function refundPlan(
  tenders: RefundTenderLike[],
  previousRefund: number,
  refundAmount: number,
): RefundLeg[] {
  if (!supportsRefund(tenders) || refundAmount <= 0) return [];
  const amounts = new Map(
    tenders.map((tender) => [tender.tender_type, parseNtd(tender.amount) ?? 0]),
  );
  const totalPaid = [...amounts.values()].reduce((total, amount) => total + amount, 0);
  if (previousRefund < 0 || previousRefund + refundAmount > totalPaid) return [];

  const priority: RefundTenderType[] = amounts.has("STORE_CREDIT")
    ? [
        "STORE_CREDIT",
        ...([...amounts.keys()].filter(
          (type) => type !== "STORE_CREDIT",
        ) as RefundTenderType[]),
      ]
    : [...amounts.keys()];
  const plan: RefundLeg[] = [];
  let priorityCapacity = 0;
  for (const tenderType of priority) {
    const capacity = amounts.get(tenderType) ?? 0;
    const refundedBefore = Math.min(
      capacity,
      Math.max(0, previousRefund - priorityCapacity),
    );
    const refundedAfter = Math.min(
      capacity,
      Math.max(0, previousRefund + refundAmount - priorityCapacity),
    );
    const delta = refundedAfter - refundedBefore;
    if (delta > 0) plan.push({ tender_type: tenderType, amount: delta });
    priorityCapacity += capacity;
  }
  return plan;
}

export const refundTenderLabel: Record<RefundTenderType, string> = {
  CASH: "現金",
  STORE_CREDIT: "購物金",
  LINE_PAY: "LINE Pay",
  TAIWAN_PAY: "台灣Pay",
};
