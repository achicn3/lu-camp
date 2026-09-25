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
  { key: "confirm", label: "叫號確認", statuses: ["AWAITING_CONFIRM"] },
  { key: "pay", label: "簽署付款", statuses: ["SIGNED"] },
  { key: "list", label: "整理上架", statuses: ["PAID", "PARTIALLY_LISTED", "LISTED"] },
];

/** 每個狀態的「下一步」提示（店員看了就知道要按什麼）。 */
export const NEXT_STEP: Record<Status, string> = {
  PENDING_ESTIMATE: "在下方「新增一件商品」逐件估價。",
  ESTIMATING: "繼續新增商品；全部估完按最下面的「估完，送去叫號」。",
  AWAITING_CONFIRM: "叫號請客人過來，逐列選「接受／客人不售／店家不收」並按儲存；簽署與付款下一期開放。",
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
