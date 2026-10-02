// 測試共用：模擬店內 backend 對 /integration/* 簽章（與 backend 端同一套規則，見 src/auth.ts）。
import { exports } from "cloudflare:workers";

export const SECRET = "test-integration-secret";

function hex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sha256Hex(data: Uint8Array | string): Promise<string> {
  const bytes = typeof data === "string" ? new TextEncoder().encode(data) : data;
  return hex(await crypto.subtle.digest("SHA-256", bytes));
}

export async function sign(
  method: string,
  path: string,
  body: Uint8Array | string,
  opts: { timestamp?: number; nonce?: string; secret?: string } = {},
): Promise<Record<string, string>> {
  const timestamp = String(opts.timestamp ?? Math.floor(Date.now() / 1000));
  const nonce = opts.nonce ?? crypto.randomUUID();
  const canonical = [method, path, timestamp, nonce, await sha256Hex(body)].join("\n");
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(opts.secret ?? SECRET),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = hex(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(canonical)));
  return {
    "X-LuCamp-Timestamp": timestamp,
    "X-LuCamp-Nonce": nonce,
    "X-LuCamp-Signature": signature,
  };
}

export async function integration(
  method: string,
  path: string,
  body: Uint8Array | string = "",
  extra: Record<string, string> = {},
  signOpts: Parameters<typeof sign>[3] = {},
): Promise<Response> {
  const headers = { ...(await sign(method, path, body, signOpts)), ...extra };
  return exports.default.fetch(
    new Request(`https://order.test${path}`, {
      method,
      headers,
      body: method === "GET" ? undefined : body,
    }),
  );
}

export function get(path: string, headers: Record<string, string> = {}): Promise<Response> {
  return exports.default.fetch(new Request(`https://order.test${path}`, { headers }));
}

export const SNAPSHOT = {
  version: 1,
  published_at: "2026-10-02T03:00:00Z",
  store_name: "露坑",
  categories: [{ id: 1, name: "咖啡" }],
  items: [
    {
      id: 5,
      name: "拿鐵",
      description: "濃縮咖啡加鮮奶",
      category_id: 1,
      unit_price: 150,
      photo: null,
      available: true,
      remaining: null,
      option_groups: [],
    },
  ],
  font: null,
};

export function webp(extra = 0): Uint8Array {
  // 最小可辨識的 WebP 檔頭：RIFF....WEBPVP8 ；內容本身不必是完整圖片（Worker 只驗檔頭與雜湊）。
  const head = new TextEncoder().encode("RIFF\u0000\u0000\u0000\u0000WEBPVP8 ");
  const out = new Uint8Array(head.length + 8 + extra);
  out.set(head);
  return out;
}

export function woff2(): Uint8Array {
  const out = new Uint8Array(32);
  out.set(new TextEncoder().encode("wOF2"));
  return out;
}
