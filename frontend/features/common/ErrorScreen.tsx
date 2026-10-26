"use client";
// 畫面出錯時的共用錯誤頁（app/(authed)/error.tsx、app/global-error.tsx 共用）。
//
// 2026-10-26：LINE Pay 結帳時整頁變成 Next 預設的黑底「Application error」，店員只看到一行
// 英文、無從回報，伺服器日誌也看不到前端錯誤。這頁把**實際的錯誤內容**攤在畫面上讓店員拍照，
// 並給「重新載入」與「回到 POS」兩個出口。
//
// 不用 Next 的 unstable_retry：它只重繪頁面，瀏覽器裡快取的壞資料還在，按了會原地再壞一次
// （煙霧實測）。整頁重新載入才乾淨；POS 的結帳冪等鍵存在 localStorage，重新載入不會重複扣款。
// 錯誤內容只顯示在本機畫面，不送任何地方、不寫 log。
import { useState } from "react";

const STACK_LINES = 8;

export type ErrorInfo = {
  name: string;
  message: string;
  digest: string | null;
  stackLines: string[];
};

/** 取出要顯示的錯誤資訊；堆疊只留前幾行（第一行是重複的「名稱: 訊息」，略過）。 */
export function describeError(error: Error & { digest?: string }): ErrorInfo {
  const stackLines = (error.stack ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("at ") || line.includes("@"))
    .slice(0, STACK_LINES);
  return {
    name: error.name || "Error",
    message: error.message || "（沒有錯誤訊息）",
    digest: error.digest ?? null,
    stackLines,
  };
}

function reloadPage() {
  window.location.reload();
}

export function ErrorScreen({
  error,
  onReload = reloadPage,
}: {
  error: Error & { digest?: string };
  /** 測試用注入；預設整頁重新載入。 */
  onReload?: () => void;
}) {
  const info = describeError(error);
  // 定格在出錯當下：之後重繪不要讓時間跳動，照片上的時間才對得上後端紀錄。
  const [occurredAt] = useState(() => new Date().toLocaleString("zh-TW", { hour12: false }));
  const path = typeof window === "undefined" ? "" : window.location.pathname;
  return (
    <section className="error-screen">
      <div className="card">
        <h2>畫面發生錯誤</h2>
        <p>
          請先<strong>拍照</strong>把這個畫面傳給店主，再按「重新載入」或回到 POS。
          若剛按了結帳，交易可能已經成立，請到「交易紀錄」確認，不要直接重新收款。
        </p>
        <p role="alert" className="form-error">
          {info.name}：{info.message}
        </p>
        <dl className="error-screen-meta">
          <dt>時間</dt>
          <dd>{occurredAt}</dd>
          {path !== "" && (
            <>
              <dt>頁面</dt>
              <dd>{path}</dd>
            </>
          )}
          {info.digest !== null && (
            <>
              <dt>代碼</dt>
              <dd>{info.digest}</dd>
            </>
          )}
        </dl>
        {info.stackLines.length > 0 && <pre>{info.stackLines.join("\n")}</pre>}
        <div className="error-screen-actions">
          <button type="button" className="btn-primary" onClick={onReload}>
            重新載入
          </button>
          {/* 整頁導向（不用 next/link）：出錯後的前端狀態不可信，重新載入最乾淨。 */}
          <a className="btn-ghost" href="/pos">
            回到 POS 結帳
          </a>
        </div>
      </div>
    </section>
  );
}
