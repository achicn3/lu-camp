"use client";
// 對話框的鍵盤焦點處理（共用）。
//
// `aria-modal` 只對輔助技術宣告「背景不可用」，**不會擋住 Tab**：焦點留在背景時
// 按幾下 Tab 就走到背後那一列的「退貨」，Enter 就在唯讀視窗背後開了退貨流程
// （Codex 審查實測）。所以要自己做三件事：開啟時把焦點移進來、開著時不讓它離開、
// 關閉時還給原本那顆按鈕（否則鍵盤使用者會被丟回頁首）。
import { useEffect, useRef } from "react";

const FOCUSABLE =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

/** 回傳要掛在對話框最外層的 ref；該元素需可聚焦（tabIndex={-1}）。 */
export function useDialogFocus<T extends HTMLElement>() {
  const ref = useRef<T>(null);

  useEffect(() => {
    const node = ref.current;
    if (node === null) return;
    const opener = document.activeElement as HTMLElement | null;
    const focusable = (): HTMLElement[] =>
      Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => !el.hasAttribute("disabled") && el.getAttribute("aria-hidden") !== "true",
      );

    (focusable()[0] ?? node).focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return;
      const items = focusable();
      if (items.length === 0) {
        event.preventDefault();
        node.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      const leavingForward = !event.shiftKey && (active === last || !node.contains(active));
      const leavingBackward = event.shiftKey && (active === first || !node.contains(active));
      if (leavingForward) {
        event.preventDefault();
        first.focus();
      } else if (leavingBackward) {
        event.preventDefault();
        last.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      // 關閉後把焦點還給打開它的那顆按鈕（元素可能已消失，故先確認還在文件裡）。
      if (opener !== null && document.contains(opener)) opener.focus();
    };
  }, []);

  return ref;
}
