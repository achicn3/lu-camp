// 硬體代理（hardware-agent）client：列印走獨立服務（預設 :8001），與後端 API 分離。
// 後端只記稽核；實體列印（ESC/POS → EPSON）由代理負責。位址由 NEXT_PUBLIC_AGENT_URL 設定。
import type { components } from "./api-types";

const AGENT_BASE = (
  process.env.NEXT_PUBLIC_AGENT_URL ?? "http://localhost:8001"
).replace(/\/+$/, "");

type SaleRead = components["schemas"]["SaleRead"];

async function postAgent(path: string, body: unknown): Promise<void> {
  let res: Response;
  try {
    res = await globalThis.fetch(`${AGENT_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("無法連線硬體代理（請確認櫃檯設備服務）");
  }
  if (!res.ok) {
    let detail = `硬體操作失敗（${res.status}）`;
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (typeof j.detail === "string") detail = j.detail;
    } catch {
      /* 非 JSON 回應沿用預設訊息 */
    }
    throw new Error(detail);
  }
}

/**
 * 送商品明細聯到硬體代理列印（含活動折扣留痕）。
 * 直接把後端 SaleRead 轉送（欄位相容 agent SalePayload），並補上活動名（代理只印、不算）。
 */
export async function printSaleDetail(
  sale: SaleRead,
  campaignName: string | null,
): Promise<void> {
  await postAgent("/print/detail", { ...sale, campaign_name: campaignName });
}

/**
 * 送商品標籤（Brother 標籤機）：條碼=code（序號品 item_code / 散裝 lot_code）、品名、整數元售價。
 */
export async function printLabel(
  code: string,
  name: string,
  price: number,
): Promise<void> {
  await postAgent("/print/label", { code, name, price });
}

/**
 * 開錢櫃（EPSON 踢櫃，docs/10 §5）：現金收付時觸發。
 * 呼叫端須把失敗當「非阻擋」處理——交易已寫後端，代理離線只提示、不可擋流程。
 */
export async function openCashDrawer(): Promise<void> {
  await postAgent("/drawer/open", {});
}
