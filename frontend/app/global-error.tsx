"use client";
// 根 layout（含受保護區殼層）本身出錯時的錯誤頁：取代整個根 layout，須自帶 html/body 與樣式。
// 內容見 features/common/ErrorScreen。
import { useEffect } from "react";

import { ErrorScreen } from "@/features/common/ErrorScreen";

import "./globals.css";

export default function GlobalError({ error }: { error: Error & { digest?: string } }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <html lang="zh-TW">
      <body>
        <ErrorScreen error={error} />
      </body>
    </html>
  );
}
