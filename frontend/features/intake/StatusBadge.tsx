// 排隊收購的狀態標籤與流程進度（docs/42 §3）：不同狀態不同顏色，一眼看出這一批走到哪、下一步做什麼。
import { STATUS_LABEL } from "@/features/intake/labels";
import type { components } from "@/lib/api-types";

type Status = components["schemas"]["IntakeBatchStatus"];

export function StatusBadge({ status }: { status: Status }) {
  return <span className={`intake-status intake-status-${status.toLowerCase()}`}>{STATUS_LABEL[status]}</span>;
}

const STEPS: { key: string; label: string; statuses: Status[] }[] = [
  { key: "checkin", label: "報到收件", statuses: [] },
  { key: "estimate", label: "估價", statuses: ["PENDING_ESTIMATE", "ESTIMATING"] },
  { key: "confirm", label: "客人確認", statuses: ["AWAITING_CONFIRM"] },
  { key: "pay", label: "簽署付款", statuses: ["SIGNED"] },
  { key: "list", label: "整理上架", statuses: ["PAID", "PARTIALLY_LISTED", "LISTED"] },
];

/** 每個狀態的「下一步」提示（店員看了就知道要按什麼）。 */
export const NEXT_STEP: Record<Status, string> = {
  PENDING_ESTIMATE: "逐件填收購價（按「下一個」跳下一件）；全部填好按「估完，給客人確認」。",
  ESTIMATING: "逐件填收購價（按「下一個」跳下一件）；全部填好按「估完，給客人確認」。",
  AWAITING_CONFIRM:
    "叫號請客人過來，按「交給客人勾選」把平板給客人勾要賣哪幾件；確認後送顧客螢幕簽切結書，再按付款。",
  SIGNED: "客人已簽署，等待付款。",
  PAID: "已付款，等空檔整理上架。",
  PARTIALLY_LISTED: "部分已上架，剩下的等空檔整理。",
  LISTED: "全部上架完成。",
  CANCELLED: "這一批已取消；沒收的商品請逐列記錄是否已交還客人。",
};

export function IntakeSteps({ status }: { status: Status }) {
  if (status === "CANCELLED") return null;
  const current = STEPS.findIndex((s) => s.statuses.includes(status));
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
