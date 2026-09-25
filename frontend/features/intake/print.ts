// 收件單列印（docs/42 裁示 3）：由批次資料組代理的列印內容。列印失敗不擋流程——號碼已登記，可補印。
import type { components } from "@/lib/api-types";
import { printIntakeSlip } from "@/lib/agent";
import { decodeSession } from "@/lib/auth";

type Batch = components["schemas"]["IntakeBatchRead"];

export async function printSlip(batch: Batch, copies = 2): Promise<void> {
  const session = decodeSession();
  if (session === null) throw new Error("登入已失效，請重新登入後補印");
  await printIntakeSlip({
    storeId: session.storeId,
    batchId: batch.id,
    label: batch.ticket_label,
    slipCode: batch.slip_code,
    sellerName: batch.contact_name,
    declaredItemCount: batch.declared_item_count,
    createdAt: batch.created_at,
    copies,
  });
}
