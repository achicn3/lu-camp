// 建置前檢查：`NEXT_PUBLIC_*` 的位址是**建置當下寫死進 JS 檔**的，不是啟動時讀的。
//
// 沒設時程式會退回 http://localhost:*，而那在單機上「看起來是對的」——收銀台這台
// 瀏覽器打 localhost 正常，但顧客螢幕的 iPad、手持簽署裝置是**另一台機器**，它們的
// localhost 指向自己，於是連不到後端。這種一半能用的故障最難查，所以寧可在建置就擋下。
//
// 由 next.config.ts 呼叫——**必須在那裡執行**：Next 會自己載入 .env/.env.local，
// 獨立的 prebuild 腳本用 node 直接跑看不到那些值，會把設定好的專案誤判成沒設定。
export function assertBuildAddresses() {
  const REQUIRED = [
    ["NEXT_PUBLIC_API_BASE_URL", "後端 API 位址"],
    ["NEXT_PUBLIC_AGENT_URL", "硬體代理位址"],
  ];

  const missing = REQUIRED.filter(([name]) => !(process.env[name] ?? "").trim());

  if (missing.length > 0) {
    console.error("\n建置中止：以下位址沒有設定，會被寫死成 localhost。\n");
    for (const [name, what] of missing) console.error(`  ${name}（${what}）`);
    console.error(
      "\n這兩個位址在建置時就寫進 JS，之後改環境變數不會生效。請填其他裝置也連得到的\n" +
        "位址（本機的固定內網 IP，例如 http://192.168.0.10:8000），不要用 localhost——\n" +
        "顧客螢幕與簽署裝置是另一台機器，它們的 localhost 指向自己。\n" +
        "設定位置：frontend/.env.local\n",
    );
    throw new Error("NEXT_PUBLIC 位址未設定");
  }

  console.log("建置位址：");
  for (const [name] of REQUIRED) console.log(`  ${name}=${process.env[name]}`);
  const local = REQUIRED.filter(([n]) => /localhost|127\.0\.0\.1/.test(process.env[n] ?? ""));
  if (local.length > 0) {
    // 不擋——單機開發本來就用 localhost；但正式機這樣設，另外那兩台裝置會連不上。
    console.warn(
      `  ⚠ ${local.map(([n]) => n).join("、")} 指向本機。` +
        "正式機請改用固定內網 IP，否則顧客螢幕/簽署裝置連不到。",
    );
  }
}
