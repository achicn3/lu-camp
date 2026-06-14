// 冪等鍵產生：crypto.randomUUID 僅在安全情境（HTTPS／localhost）存在；門市為 LAN HTTP
// （docs/10 §9），該情境下 randomUUID 為 undefined。改用各情境皆可用的 getRandomValues
// 後備，最後再退到時間戳＋亂數，確保結帳頁在 LAN HTTP 也能產生 Idempotency-Key。
export function newIdempotencyKey(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID();
  }
  if (c && typeof c.getRandomValues === "function") {
    const b = c.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40; // version 4
    b[8] = (b[8] & 0x3f) | 0x80; // variant
    const h = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  }
  return `idem-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
