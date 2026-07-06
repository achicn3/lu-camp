// 手持簽署裝置專用外殼（docs/23 K3）：**不繼承 (authed) 店務殼**——無頂欄、無導覽，
// 客人面向的全螢幕頁面。與店務 App 同源共用 token store，但此裝置以 KIOSK 帳號登入，
// 只會呼叫 /kiosk 端點（後端 D4 中央圍堵：KIOSK token 打店務端點一律 403）。
import type { ReactNode } from "react";

export default function KioskLayout({ children }: { children: ReactNode }) {
  return <div className="kiosk-root">{children}</div>;
}
