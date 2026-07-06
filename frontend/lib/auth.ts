// 認證：登入、登出、JWT payload 解碼（僅供 UI 顯示；權威驗證在後端）。
import { api } from "./api";
import { clearToken, getToken, setToken } from "./token";

export { clearToken, getToken, setToken } from "./token";

export interface Session {
  userId: number;
  role: "MANAGER" | "CLERK";
  storeId: number;
}

export type LoginResult = { ok: true } | { ok: false; status: number; message: string };

/** 登入：成功存 token；失敗回後端訊息（401 帳密錯誤 / 429 節流），不丟例外。 */
export async function login(username: string, password: string): Promise<LoginResult> {
  try {
    const { data, error, response } = await api.POST("/api/v1/auth/login", {
      body: { username, password },
    });
    if (data) {
      setToken(data.access_token);
      return { ok: true };
    }
    const detail =
      error && typeof error === "object" && "detail" in error && typeof error.detail === "string"
        ? error.detail
        : "登入失敗，請再試一次";
    return { ok: false, status: response.status, message: detail };
  } catch {
    return { ok: false, status: 0, message: "無法連線到伺服器，請確認店內網路" };
  }
}

export function logout(): void {
  clearToken();
}

/** 讀 JWT 內的 role 原字串（含 KIOSK；供路由閘門判斷），無 token/壞格式 → null。 */
export function readTokenRole(): string | null {
  const token = getToken();
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload: unknown = JSON.parse(atob(base64));
    if (payload === null || typeof payload !== "object") return null;
    const role = (payload as Record<string, unknown>).role;
    return typeof role === "string" ? role : null;
  } catch {
    return null;
  }
}

/** 解 JWT payload（base64url）取 UI 用身分資訊；無 token / 格式壞 → null。 */
export function decodeSession(): Session | null {
  const token = getToken();
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload: unknown = JSON.parse(atob(base64));
    if (payload === null || typeof payload !== "object") return null;
    const record = payload as Record<string, unknown>;
    if (
      typeof record.sub !== "string" ||
      (record.role !== "MANAGER" && record.role !== "CLERK") ||
      typeof record.store_id !== "number"
    ) {
      return null;
    }
    return { userId: Number(record.sub), role: record.role, storeId: record.store_id };
  } catch {
    return null;
  }
}
