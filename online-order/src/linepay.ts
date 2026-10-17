// LINE Pay Online API v4 客戶端（docs/44 §4.4、§4.4.1；O5a）。只在 Worker 端呼叫，憑證放 Worker secrets。
// 簽章與店內 Offline 相同：base64(HMAC-SHA256(secret, secret + apiPath + body(或 query) + nonce))。
// transactionId 是 19 位數字，超過 JS 安全整數：一律從**原始回應文字**取字串，不用 JSON.parse 後的數字。

export interface LinePayConfig {
  channelId: string;
  channelSecret: string;
  apiBase: string;
  /** 讀取逾時；預設 20 秒（官方 confirm／check 下限）。補查用較短的，結果不明下次再查。 */
  timeoutMs?: number;
}

export interface LinePayReply {
  /** null＝沒拿到可辨識的回應（逾時、連不上、壞 JSON）：結果不明，不可當成失敗。 */
  code: string | null;
  message: string;
  transactionId: string | null;
  paymentUrl: string | null;
  /** 客人 LINE Pay 綁定的手機條碼載具（請款回應 `info.merchantReference`）；沒綁、沒開通或不成形＝null。 */
  carrier: string | null;
}

// 手機條碼載具的碼身：7 碼（數字／大寫英文／+-.），標準寫法前面加 /。
const CARRIER_BODY = /^[0-9A-Z+\-.]{7}$/;

/**
 * 從 `info.merchantReference.affiliateCards[]` 取客人綁定的載具（店主 2026-10-17；merchantReference 要向
 * LINE Pay 申請開通才會回傳，TW only）。規則與店內 Offline 收款相同（backend sales/linepay.py `_mobile_carrier`）：
 * 只認 `cardType == "MOBILE_CARRIER"`；文件沒規定 `cardId` 帶不帶 /，兩種都收並補上 /；
 * 不成形的當沒有——寧可印紙本，也不能拿不合格的字去開發票。付款已成功，解析失敗不影響付款。
 */
function boundCarrier(info: unknown): string | null {
  if (typeof info !== "object" || info === null) return null;
  const reference = (info as { merchantReference?: unknown }).merchantReference;
  if (typeof reference !== "object" || reference === null) return null;
  const cards = (reference as { affiliateCards?: unknown }).affiliateCards;
  if (!Array.isArray(cards)) return null;
  for (const card of cards) {
    if (typeof card !== "object" || card === null) continue;
    const { cardType, cardId } = card as { cardType?: unknown; cardId?: unknown };
    if (cardType !== "MOBILE_CARRIER" || typeof cardId !== "string") continue;
    const body = cardId.trim().replace(/^\//, "");
    if (CARRIER_BODY.test(body)) return `/${body}`;
  }
  return null;
}

// 官方要求 confirm／check 讀取逾時至少 20 秒。
const TIMEOUT_MS = 20_000;

export function linePayConfig(env: Env): LinePayConfig | null {
  if (!env.LINEPAY_CHANNEL_ID || !env.LINEPAY_CHANNEL_SECRET || !env.LINEPAY_API_BASE) return null;
  return { channelId: env.LINEPAY_CHANNEL_ID, channelSecret: env.LINEPAY_CHANNEL_SECRET, apiBase: env.LINEPAY_API_BASE };
}

export async function lineSignature(secret: string, path: string, body: string, nonce: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const raw = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(secret + path + body + nonce));
  return btoa(String.fromCharCode(...new Uint8Array(raw)));
}

export function parseLinePay(text: string): LinePayReply {
  let parsed: { returnCode?: unknown; returnMessage?: unknown; info?: { paymentUrl?: { web?: unknown }; merchantReference?: unknown } };
  try {
    parsed = JSON.parse(text) as typeof parsed;
  } catch {
    return { code: null, message: "unreadable reply", transactionId: null, paymentUrl: null, carrier: null };
  }
  const raw = /"transactionId"\s*:\s*"?(\d{1,20})"?/.exec(text);
  const web = parsed.info?.paymentUrl?.web;
  return {
    code: typeof parsed.returnCode === "string" ? parsed.returnCode : null,
    message: typeof parsed.returnMessage === "string" ? parsed.returnMessage : "",
    transactionId: raw?.[1] ?? null,
    paymentUrl: typeof web === "string" ? web : null,
    carrier: boundCarrier(parsed.info),
  };
}

async function call(config: LinePayConfig, method: "GET" | "POST", path: string, body = ""): Promise<LinePayReply> {
  const nonce = crypto.randomUUID();
  try {
    const resp = await fetch(`${config.apiBase}${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-LINE-ChannelId": config.channelId,
        "X-LINE-Authorization-Nonce": nonce,
        // GET 以 query string 代替 body；本檔的 GET 都沒有 query。
        "X-LINE-Authorization": await lineSignature(config.channelSecret, path, body, nonce),
      },
      body: method === "POST" ? body : undefined,
      signal: AbortSignal.timeout(config.timeoutMs ?? TIMEOUT_MS),
    });
    return parseLinePay(await resp.text());
  } catch {
    return { code: null, message: "transport error", transactionId: null, paymentUrl: null, carrier: null };
  }
}

/** 要付款連結。`orderId` 每次重付都不同（LINE Pay 不收重複的 orderId）。 */
export function requestPayment(
  config: LinePayConfig,
  args: { orderId: string; amount: number; productName: string; confirmUrl: string; cancelUrl: string },
): Promise<LinePayReply> {
  const body = JSON.stringify({
    amount: args.amount,
    currency: "TWD",
    orderId: args.orderId,
    packages: [{
      id: "1", amount: args.amount, name: "露坑",
      products: [{ name: args.productName, quantity: 1, price: args.amount }],
    }],
    redirectUrls: { confirmUrl: args.confirmUrl, cancelUrl: args.cancelUrl },
  });
  return call(config, "POST", "/v4/payments/request", body);
}

/** 請款：金額必須等於訂單金額。 */
export function confirmPayment(config: LinePayConfig, transactionId: string, amount: number): Promise<LinePayReply> {
  return call(config, "POST", `/v4/payments/${transactionId}/confirm`, JSON.stringify({ amount, currency: "TWD" }));
}

/** 補查：0000＝客人還沒付、0110＝已授權未請款、0123＝已完成。 */
export function checkPayment(config: LinePayConfig, transactionId: string): Promise<LinePayReply> {
  return call(config, "GET", `/v4/payments/requests/${transactionId}/check`);
}
