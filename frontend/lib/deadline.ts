// 有期限的等待：**收銀台永遠不能因為某支請求不回應而卡死**。
//
// 伺服器收了連線卻不回應時，promise 既不 resolve 也不 reject，try/finally 接不住，
// 依賴它解鎖的 UI 就會永久停用。凡是擋著結帳流程的請求都要套上期限。
export const RESTORE_LOOKUP_TIMEOUT_MS = 3000;

/**
 * 競速：先到者為準。逾時回 `fallback`，不等原本那個 promise。
 * `onTimeout` 可用來中止底層請求，避免逾時後仍占用連線或觸發全域副作用。
 */
export function withDeadline<T>(
  pending: Promise<T>,
  timeoutMs: number,
  fallback: T,
  onTimeout?: () => void,
): Promise<T> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      onTimeout?.();
      resolve(fallback);
    }, timeoutMs);
    void pending.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      () => {
        clearTimeout(timer);
        resolve(fallback);
      },
    );
  });
}
