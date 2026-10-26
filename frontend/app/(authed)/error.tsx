"use client";
// 受保護區內任何頁面出錯時的錯誤頁（頂欄導覽仍在）。內容見 features/common/ErrorScreen。
import { useEffect } from "react";

import { ErrorScreen } from "@/features/common/ErrorScreen";

export default function AuthedError({ error }: { error: Error & { digest?: string } }) {
  useEffect(() => {
    // 留一份在瀏覽器主控台，開發者工具裡看得到完整堆疊。
    console.error(error);
  }, [error]);
  return <ErrorScreen error={error} />;
}
