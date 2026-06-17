// access token 儲存：記憶體 + localStorage（使用者裁示 2026-06-18「登入永不過期」）。
// ⚠️ 改用 localStorage（先前為 sessionStorage）讓 session 跨瀏覽器重開仍保留——與後端
// 永不過期 token 搭配，達成「永不登出」。安全代價（token 落磁碟、不隨關閉清除）由 D-4
// 伺服器端重驗緩解；設後端 auth_session_never_expires=false 並改回 sessionStorage 即恢復短效。
// 獨立成小模組（無相依），讓 lib/api 與 lib/auth 都能引用而不形成循環。

const STORAGE_KEY = "lu-camp.access-token";

function store(): Storage | null {
  return typeof window === "undefined" ? null : window.localStorage;
}

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
  inMemoryToken = store()?.getItem(STORAGE_KEY) ?? null;
  return inMemoryToken;
}

export function setToken(token: string): void {
  inMemoryToken = token;
  if (typeof window !== "undefined") {
    store()?.setItem(STORAGE_KEY, token);
  }
  emitChange();
}

export function clearToken(): void {
  inMemoryToken = null;
  if (typeof window !== "undefined") {
    store()?.removeItem(STORAGE_KEY);
  }
  emitChange();
}

/** 401 時由 api 層廣播、(authed) layout 監聽導回登入頁。 */
export const UNAUTHORIZED_EVENT = "lu-camp:unauthorized";
