import type { NextConfig } from "next";

import { assertBuildAddresses } from "./scripts/check-build-env.mjs";

// Next 在讀這份設定之前已載入 .env / .env.local，所以位址檢查放這裡才看得到值
// （獨立的 prebuild 腳本用 node 直接跑會看不到 .env.local，實測會誤報建置中止）。
assertBuildAddresses();

const nextConfig: NextConfig = {
  devIndicators: false,
  // **只影響 `next dev`**：Next 預設封鎖非 localhost 來源對開發資源（HMR 等）的請求。
  // 本機 WSL2 的 localhost 轉發壞掉時只能用 WSL 的 IP 連，於是 HMR 被擋、
  // React 完全不 hydrate——症狀是「畫面只有背景色」「登入表單以 GET 送出、
  // 帳密跑到網址列」，而 JS 檔全部 200、看不出哪裡錯。
  // 172.31.x.x 是 WSL NAT 網段；正式環境用 `next build` 不受此設定影響。
  // 不寫死單一 IP：WSL 每次重開機都會換一個。172.31/172.2x 是 WSL NAT 網段，
  // 192.168/10.x 是店內區網（平板連顧客螢幕時會用到）。
  allowedDevOrigins: ["172.31.*.*", "172.2*.*.*", "192.168.*.*", "10.*.*.*"],
};

export default nextConfig;
