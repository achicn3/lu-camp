// 排隊收購的狀態標籤與流程進度（docs/42 §3）：不同狀態不同顏色，一眼看出這一批走到哪、下一步做什麼。
import { STATUS_LABEL } from "@/features/intake/labels";
import type { components } from "@/lib/api-types";

type Status = components["schemas"]["IntakeBatchStatus"];

export function StatusBadge({ status }: { status: Status }) {
  return <span className={`intake-status intake-status-${status.toLowerCase()}`}>{STATUS_LABEL[status]}</span>;
}

// 估完之後（客人勾選、簽署、付款、整理上架）不在這條進度上：勾選與簽署在同一台平板一次做完，
// 整理上架有自己的「待整理」清單（docs/42 §13；店主 2026-10-02）。
const STEPS: { key: string; label: string; statuses: Status[] }[] = [
  { key: "checkin", label: "報到收件", statuses: [] },
  { key: "estimate", label: "估價", statuses: ["PENDING_ESTIMATE", "ESTIMATING"] },
];

/** 每個狀態的「下一步」提示（店員看了就知道要按什麼）。 */
export const NEXT_STEP: Record<Status, string> = {
  PENDING_ESTIMATE: "逐件點類型、填價格（按「下一個」跳下一件）；全部填好按「估完，給客人確認」。",
  ESTIMATING: "逐件點類型、填價格（按「下一個」跳下一件）；全部填好按「估完，給客人確認」。",
  AWAITING_CONFIRM:
    "叫號請客人過來，按「交給客人勾選」把平板給客人：勾要賣哪幾件、簽切結書、選現金或購物金；交還後按付款。",
  SIGNED: "客人已簽署，等待付款。",
  PAID: "已付款，等空檔整理上架。",
  PARTIALLY_LISTED: "部分已上架，剩下的等空檔整理。",
  LISTED: "全部上架完成。",
  CANCELLED: "這一批已取消；沒收的商品請逐列記錄是否已交還客人。",
};

export function IntakeSteps({ status }: { status: Status }) {
  if (status === "CANCELLED") return null;
  const found = STEPS.findIndex((s) => s.statuses.includes(status));
  const current = found === -1 ? STEPS.length : found;
  return (
    <ol className="intake-steps" aria-label="流程進度">
      {STEPS.map((step, index) => (
        <li
          key={step.key}
          className={index < current ? "done" : index === current ? "current" : undefined}
          aria-current={index === current ? "step" : undefined}
        >
          {step.label}
        </li>
      ))}
    </ol>
  );
}
