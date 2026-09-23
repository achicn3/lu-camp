// 報表頁分成 5 組（2026-09-23）：煙霧與手冊腳本切到某張報表都走這裡——先點分組、再點報表。
// 分組對照與畫面上的順序一致（app/(authed)/reports/page.tsx 的 GROUPS）。
export const REPORT_GROUPS = {
  今日營運: "每天看",
  現金對帳: "每天看",
  銷售毛利: "賣得如何",
  經營洞察: "賣得如何",
  趨勢: "賣得如何",
  "餐飲內用/外帶": "賣得如何",
  活動成效: "促銷",
  臨時折扣: "促銷",
  贈品: "促銷",
  庫存價值: "帳務",
  寄售應付: "帳務",
  發票月報: "帳務",
  購物金餘額: "購物金",
  購物金進出: "購物金",
  購物金效益: "購物金",
  購物金對帳: "購物金",
};

/** 切到指定報表（名稱要與畫面完全相同，例如「購物金對帳」而不是「對帳」）。 */
export async function openReport(page, name) {
  const group = REPORT_GROUPS[name];
  if (!group) throw new Error(`未知的報表名稱：${name}（見 scripts/_reports.mjs）`);
  await page
    .getByRole("tablist", { name: "報表分類" })
    .getByRole("tab", { name: group, exact: true })
    .click();
  await page
    .getByRole("tablist", { name: "報表", exact: true })
    .getByRole("tab", { name, exact: true })
    .click();
}
