// 收購佇列的畫面用語（docs/42）：狀態、處置、類型。
import type { components } from "@/lib/api-types";

type Status = components["schemas"]["IntakeBatchStatus"];
type Disposition = components["schemas"]["IntakeDisposition"];

export const STATUS_LABEL: Record<Status, string> = {
  PENDING_ESTIMATE: "待估價",
  ESTIMATING: "估價中",
  AWAITING_CONFIRM: "待確認（等叫號）",
  SIGNED: "已簽署待付款",
  PAID: "已付款待整理",
  PARTIALLY_LISTED: "部分上架",
  LISTED: "全部上架",
  CANCELLED: "已取消",
};

export const DISPOSITION_LABEL: Record<Disposition, string> = {
  PENDING: "還沒談定",
  ACCEPTED: "接受",
  CUSTOMER_KEPT: "客人不售",
  STORE_DECLINED: "店家不收",
};
