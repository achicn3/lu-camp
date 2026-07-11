// 簽名影像抓取（docs/23 K6）：自後端取已簽任務的 PNG 原圖並轉 base64（供憑證聯列印）。
// openapi-fetch 以 JSON 為主，二進位回應改用原生 fetch 帶 Bearer token。
import { getToken } from "./token";

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** 取簽名 PNG（base64，不含 data: 前綴）；未簽/不存在 → 擲錯。 */
export async function fetchSignaturePngBase64(taskId: number): Promise<string> {
  const res = await fetch(`${BASE_URL}/api/v1/signing/tasks/${taskId}/signature`, {
    headers: { Authorization: `Bearer ${getToken() ?? ""}` },
  });
  if (!res.ok) throw new Error(`取得簽名影像失敗（${res.status}）`);
  const buf = new Uint8Array(await res.arrayBuffer());
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < buf.length; i += CHUNK) {
    bin += String.fromCharCode(...buf.subarray(i, i + CHUNK));
  }
  return btoa(bin);
}
