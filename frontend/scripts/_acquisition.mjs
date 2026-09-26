// 收購頁共用操作（煙霧與手冊腳本）。
// 成色自 2026-09-26 起是一排按鈕（原本是下拉選單）：scope 可以是整頁或某一列（.acq-row）。
export async function pickGrade(scope, grade) {
  await scope
    .locator('[role="radiogroup"][aria-label="成色"]')
    .first()
    .locator(`button[data-grade="${grade}"]`)
    .click();
}

// 品名收在「品名：…」摺疊區（選型號會自動帶入）：沒展開就先展開再填。scope 可以是整頁或某一列。
export async function fillItemName(scope, name) {
  const details = scope.locator(".acq-name-detail").first();
  if (!(await details.evaluate((el) => el.open))) await details.locator("summary").click();
  await details.locator('input[aria-label="品名"]').fill(name);
}

// 估計轉售價收在「其他估價方式」摺疊區：沒展開就先展開再填。
export async function fillEstimatedResale(scope, value) {
  const details = scope.locator(".acq-legacy-pricing").first();
  if (!(await details.evaluate((el) => el.open))) await details.locator("summary").click();
  await details.locator('input[aria-label="估計轉售價"]').fill(value);
}
