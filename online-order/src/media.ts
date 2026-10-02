// 菜單照片與手寫字型子集（docs/44 §3.4、§8.1 T10）：存 R2，鍵＝內容雜湊。
// 只收雜湊對得上、檔頭正確的 WebP／WOFF2；照片已由店內 backend 重新編碼、去掉 EXIF。
import { IMMUTABLE, error, sha256Hex } from "./http";

export type MediaKind = "photos" | "fonts";

const KINDS: Record<MediaKind, { ext: string; type: string; max: number; magic: (b: Uint8Array) => boolean }> = {
  photos: {
    ext: "webp",
    type: "image/webp",
    max: 2 * 1024 * 1024,
    magic: (b) => ascii(b, 0, 4) === "RIFF" && ascii(b, 8, 12) === "WEBP",
  },
  fonts: {
    ext: "woff2",
    type: "font/woff2",
    max: 1024 * 1024,
    magic: (b) => ascii(b, 0, 4) === "wOF2",
  },
};

export const HASH = /^[0-9a-f]{64}$/;

function ascii(b: Uint8Array, start: number, end: number): string {
  return String.fromCharCode(...b.subarray(start, end));
}

export function mediaLimit(kind: MediaKind): number {
  return KINDS[kind].max;
}

export async function putMedia(env: Env, kind: MediaKind, hash: string, body: Uint8Array): Promise<Response> {
  const spec = KINDS[kind];
  if (!HASH.test(hash)) return error("not_found", 404);
  if (!spec.magic(body)) return error("invalid_media", 422);
  if ((await sha256Hex(body)) !== hash) return error("hash_mismatch", 422);
  const key = `${kind}/${hash}.${spec.ext}`;
  if ((await env.MEDIA.head(key)) !== null) return new Response(null, { status: 204 });
  await env.MEDIA.put(key, body, { httpMetadata: { contentType: spec.type } });
  return new Response(null, { status: 201 });
}

export async function getMedia(env: Env, kind: MediaKind, file: string): Promise<Response> {
  const spec = KINDS[kind];
  const suffix = `.${spec.ext}`;
  const hash = file.endsWith(suffix) ? file.slice(0, -suffix.length) : "";
  if (!HASH.test(hash)) return error("not_found", 404);
  const object = await env.MEDIA.get(`${kind}/${hash}${suffix}`);
  if (object === null) return error("not_found", 404);
  return new Response(object.body, { headers: { "Content-Type": spec.type, "Cache-Control": IMMUTABLE } });
}
