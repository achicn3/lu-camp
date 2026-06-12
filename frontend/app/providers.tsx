"use client";
// 全域 Providers：TanStack Query（docs/10 §2 資料抓取/變更策略）。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          // 櫃檯內網環境：失敗快速可見（不長重試）、資料以使用者操作為主不過度自動重抓
          queries: { retry: 1, refetchOnWindowFocus: false },
        },
      }),
  );
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
