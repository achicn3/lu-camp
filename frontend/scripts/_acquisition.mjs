// 收購頁共用操作（煙霧與手冊腳本）。
// 成色自 2026-09-26 起是一排按鈕（原本是下拉選單）：scope 可以是整頁或某一列（.acq-row）。
export async function pickGrade(scope, grade) {
  await scope
    .locator('[role="radiogroup"][aria-label="成色"]')
    .first()
    .locator(`button[data-grade="${grade}"]`)
    .click();
}
