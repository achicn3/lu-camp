// 開啟 Modal 時鎖住背景（document.body）捲動，避免遮罩下的頁面仍能滾動、或關閉後捲動位置跑掉。
// 以模組層計數支援多個 Modal 疊開（全部關閉才還原）；active=false 時不鎖，供條件式對話框使用。
import { useEffect } from "react";

let lockCount = 0;
let savedOverflow = "";

export function useBodyScrollLock(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    const body = globalThis.document?.body;
    if (!body) return;
    if (lockCount === 0) {
      savedOverflow = body.style.overflow;
      body.style.overflow = "hidden";
    }
    lockCount += 1;
    return () => {
      lockCount -= 1;
      if (lockCount === 0) body.style.overflow = savedOverflow;
    };
  }, [active]);
}
