// 菜單照片網址（docs/44 §3.4）：公開端點、內容雜湊當檔名，<img> 不必帶登入。
import { API_BASE_URL } from "@/lib/api";

export const MAX_PHOTO_BYTES = 10 * 1024 * 1024;

export function menuPhotoUrl(sha256: string): string {
  return `${API_BASE_URL}/api/v1/menu-photos/${sha256}.webp`;
}
