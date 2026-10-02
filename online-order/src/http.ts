// 回應工具與安全標頭（docs/44 §8.1 T8）。靜態點餐頁的同一組標頭寫在 public/_headers。

export const SECURITY_HEADERS: Readonly<Record<string, string>> = {
  "Content-Security-Policy":
    "default-src 'self'; img-src 'self' data:; font-src 'self'; style-src 'self'; script-src 'self'; " +
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
  "X-Frame-Options": "DENY",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
};

export const IMMUTABLE = "public, max-age=31536000, immutable";

export function withSecurityHeaders(resp: Response): Response {
  const out = new Response(resp.body, resp);
  for (const [k, v] of Object.entries(SECURITY_HEADERS)) out.headers.set(k, v);
  return out;
}

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...headers },
  });
}

export function error(code: string, status: number): Response {
  return json({ error: code }, status);
}

/** 讀 body，超過上限就丟 `BodyTooLarge`；邊讀邊數，不信 Content-Length。 */
export class BodyTooLarge extends Error {}

export async function readBody(req: Request, limit: number): Promise<Uint8Array> {
  const declared = Number(req.headers.get("Content-Length") ?? "0");
  if (declared > limit) throw new BodyTooLarge();
  if (req.body === null) return new Uint8Array();
  const reader = req.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) {
      await reader.cancel();
      throw new BodyTooLarge();
    }
    chunks.push(value);
  }
  const out = new Uint8Array(size);
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.byteLength;
  }
  return out;
}

export function hex(buf: ArrayBuffer): string {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sha256Hex(data: Uint8Array): Promise<string> {
  return hex(await crypto.subtle.digest("SHA-256", data));
}
