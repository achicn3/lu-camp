// 線上 LINE Pay 開關（店主 2026-10-20）：雲端有金鑰、而且 POS 設定打開，客人才能用 LINE Pay。
// 只擋新的付款（建單、開始付款）；已經在付的（請款、補查）不看開關，錢收了就要完成。
import { linePayConfig } from "./linepay";

export async function linePayOpen(env: Env, storeId: number): Promise<boolean> {
  if (linePayConfig(env) === null) return false;
  const row = await env.DB.prepare("SELECT linepay_enabled FROM stores_meta WHERE store_id = ?")
    .bind(storeId)
    .first<{ linepay_enabled: number }>();
  return row?.linepay_enabled === 1;
}
