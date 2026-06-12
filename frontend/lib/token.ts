// access token 儲存：記憶體 + sessionStorage（docs/10 §2「token 不落不安全儲存」的
// 折衷裁示 2026-06-12：sessionStorage 重開瀏覽器即失效；refresh token 屬 D-4）。
// 獨立成小模組（無相依），讓 lib/api 與 lib/auth 都能引用而不形成循環。

const STORAGE_KEY = "lu-camp.access-token";

let inMemoryToken: string | null = null;

// token 為「外部 store」：變更時通知訂閱者（React 以 useSyncExternalStore 訂閱）。
const listeners = new Set<() => void>();

function emitChange(): void {
  for (const listener of listeners) listener();
}

export function subscribeToken(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getToken(): string | null {
  if (inMemoryToken !== null) return inMemoryToken;
  if (typeof window === "undefined") return null;
  inMemoryToken = window.sessionStorage.getItem(STORAGE_KEY);
  return inMemoryToken;
}

export function setToken(token: string): void {
  inMemoryToken = token;
  if (typeof window !== "undefined") {
    window.sessionStorage.setItem(STORAGE_KEY, token);
  }
  emitChange();
}

export function clearToken(): void {
  inMemoryToken = null;
  if (typeof window !== "undefined") {
    window.sessionStorage.removeItem(STORAGE_KEY);
  }
  emitChange();
}

/** 401 時由 api 層廣播、(authed) layout 監聽導回登入頁。 */
export const UNAUTHORIZED_EVENT = "lu-camp:unauthorized";
