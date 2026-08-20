// 根 layout：繁體中文（台灣）、設計 token（docs/10 附錄 A）、全域 Providers。
// 字體採 token 內建堆疊（本地後備），不在建置期抓 Google Fonts——店內環境離線可建置。
import type { Metadata } from "next";

import "./globals.css";

import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "露營二手 POS",
  description: "門市操作系統：結帳、收購、庫存、現金對帳",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-TW">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
