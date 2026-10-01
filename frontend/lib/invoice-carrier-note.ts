// 完成畫面是否顯示「載具由 LINE Pay 自動帶入」的提示。
//
// **抽成共用述詞而不是在測試裡抄一份**：抄一份的話，把 page.tsx 的提示整塊刪掉測試
// 照樣綠——那種測試守不住任何東西（見 070c4be「修好會說謊的測試」）。
import type { components } from "@/lib/api-types";

type InvoiceRead = components["schemas"]["InvoiceRead"];

/**
 * 店員沒打載具、發票卻有 → 那是從客人的 LINE Pay 帶進來的，要讓店員知道。
 *
 * **一定要講**：有載具就不印紙本，客人沒看到紙會以為沒開發票；客人若說「我沒有載具」，
 * 店員也要能當場發現對不上。
 * 統編／捐贈與載具至多擇一，選了那兩者發票就不會有 carrier_id，故不必另外判斷。
 *
 * **這個型別守衛是單向的**：回 `false` 不代表發票沒有載具——也可能只是店員自己打了。
 * 目前傳進來的是 `InvoiceRead | null`，否定分支不會被收窄成 `never`；但若日後有人把
 * **已經收窄過**的值傳進來，`false` 分支就會變成無法到達。要在否定分支裡判斷「有沒有
 * 載具」，請直接看 `carrier_id`，不要拿這個述詞的反面當答案。
 */
export function showsLinePayCarrierNote<T extends Pick<InvoiceRead, "carrier_id">>(
  invoice: T | null,
  clerkCarrierInput: string,
): invoice is T & { carrier_id: string } {
  return invoice != null && invoice.carrier_id != null && clerkCarrierInput === "";
}

type SaleRead = components["schemas"]["SaleRead"];

/**
 * 用 LINE Pay 付款、發票卻沒有載具（店員也沒打統編或選捐贈）→ 告訴店員
 * 「LINE Pay 沒回載具、已印紙本」（店主 2026-10-01）。
 *
 * 載具要靠 LINE Pay 的 merchantReference 帶回：沒開通、客人沒綁、或格式不合都會取不到，
 * 後端一律照常開立並印證明聯。不講原因的話，客人說「我有綁載具啊」時店員無從解釋。
 */
export function showsLinePayNoCarrierNote(
  invoice: Pick<InvoiceRead, "carrier_id" | "buyer_tax_id" | "donate_mark"> | null,
  sale: Pick<SaleRead, "tenders"> | null,
): boolean {
  if (invoice == null || sale == null) return false;
  const choseOtherwise = invoice.buyer_tax_id != null || invoice.donate_mark;
  if (invoice.carrier_id != null || choseOtherwise) return false;
  return sale.tenders.some((t) => t.tender_type === "LINE_PAY");
}
