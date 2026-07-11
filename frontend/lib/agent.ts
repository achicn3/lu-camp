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
export interface SaleDetailPrintExtras {
  // 購物金×手持簽署（docs/23 K6，D6）：折抵/剩餘與簽名影像；未提供時版面同既有。
  storeCreditDeducted?: string;
  storeCreditRemaining?: string;
  signaturePngBase64?: string;
}

export async function printSaleDetail(
  sale: SaleRead,
  campaignName: string | null,
  extras?: SaleDetailPrintExtras,
): Promise<void> {
  await postAgent("/print/detail", {
    ...sale,
    campaign_name: campaignName,
    store_credit_deducted: extras?.storeCreditDeducted ?? null,
    store_credit_remaining: extras?.storeCreditRemaining ?? null,
    signature_png_base64: extras?.signaturePngBase64 ?? null,
  });
}

export interface AcquisitionReceiptPrint {
  storeId: number;
  acquisitionId: number;
  sellerName: string;
  items: { name: string; amount: string }[];
  total: string;
  payoutMethod: string; // CASH | STORE_CREDIT
  createdAt: string; // ISO
  signaturePngBase64: string;
  storeCreditGranted?: string;
}

/** 列印收購憑證聯（docs/23 K6）：切結品項/總額/撥款＋賣方簽名（存證聯）。 */
export async function printAcquisitionReceipt(r: AcquisitionReceiptPrint): Promise<void> {
  await postAgent("/print/acquisition", {
    store_id: r.storeId,
    acquisition_id: r.acquisitionId,
    seller_name: r.sellerName,
    items: r.items,
    total: r.total,
    payout_method: r.payoutMethod,
    created_at: r.createdAt,
    signature_png_base64: r.signaturePngBase64,
    store_credit_granted: r.storeCreditGranted ?? null,
  });
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
