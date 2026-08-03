// 產生要內嵌的截圖清單（id → 檔案），供 convert-images.mjs 使用。
import { existsSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { SHOTS_ROOT } from "./_lib.mjs";

const PICK = {
  "01-shell": ["01-login-empty", "02-login-error", "03-home", "04-header", "05-nav-drawer", "06-home-mobile", "07-after-logout"],
  "02-cash": ["01-open-form", "02-opened", "03-adjust-form", "04-adjust-done", "05-adjust-error", "06-close-card"],
  "18-cash-close": ["01-before-close", "02-close-form", "03-closed-summary", "04-opening-input-blocked", "05-reopened"],
  "03-contacts": ["01-page-search-tab", "02-create-form-filled", "03-create-nid-error", "04-search-by-name", "05-search-nid-error", "06-search-nid-hit", "07-all-members", "08-all-members-filtered", "09-detail-overview", "10-detail-purchases", "11-detail-consignments", "12-detail-sourced", "13-detail-edit", "14-detail-edit-saved", "15-detail-reveal-nid"],
  "04-kiosk": ["01-kiosk-login", "02-kiosk-pairing-code", "03-pos-unpaired", "04-pos-paired-online", "05-kiosk-standby"],
  "05-acquisition": ["01-page-buyout-empty", "02-type-tabs", "03-seller-search", "04-seller-selected", "05-row-pricing-aid", "06-row-listed-buttons", "07-row-filled", "08-row-overcost-warning", "09-payout-cash", "10-result-buyout", "11-labels-printed", "12-payout-store-credit", "13-sign-waiting", "14-kiosk-affidavit-top", "14b-kiosk-affidavit-full", "15-kiosk-payout-choice", "16-kiosk-signature-drawn", "17-kiosk-signed-thanks", "18-sign-done", "19-payout-locked-by-signature", "20-result-buyout-credit", "21-receipt-printed", "22-consignment-form", "23-consignment-result", "24-bulk-form", "25-bulk-result", "26-void-lookup", "27-void-dialog", "28-void-result"],
  "06-inventory": ["01-serialized-list", "03-filters", "04-serialized-filtered", "05-serialized-detail", "06-price-dialog", "07-price-changed", "08-reprint-label", "09-aging", "10-catalog-empty", "11-catalog-create-form", "12-catalog-created", "13-bulk-list", "14-bulk-detail"],
  "07-menu-campaigns": ["01-menu-empty", "02-menu-create-form", "03-menu-list", "04-menu-edit-price", "05-menu-unavailable", "06-menu-available-again", "07-campaign-empty", "08-campaign-create-form", "09-campaign-draft", "10-campaign-active", "11-campaign-filter", "12-campaign-cancelled"],
  "08-pos": ["01-pos-empty", "02-campaign-banner", "03-kiosk-status", "04-scan-error", "05-cart-one-item", "06-menu-tiles", "07-menu-qty-dialog", "08-cart-with-menu", "09-member-search-results", "10-member-selected", "11-tender-cash", "11b-kiosk-cart", "12-complete-dialog", "13-print-dialog-done", "14-complete-screen"],
  "08-pos-mixed": ["01-mixed-panel", "03-sign-done", "01-mixed-split-taiwanpay", "02-sign-pushed", "08-kiosk-credit-task", "03-complete-mixed"],
  "08-pos-mixed-cash": ["01-mixed-cash-split", "02-mixed-cash-signed", "03-mixed-cash-complete"],
  "08-pos-mixed-linepay": ["01-tender-modes-with-linepay", "02-mixed-linepay-panel", "03-checkout-result"],
  "08-pos-gift-discount": ["01-cart", "02-item-discount-dialog", "03-item-discount-applied", "04-order-discount-dialog", "05-discount-list", "06-gift-dialog", "07-gift-cart", "08-summary", "09-completed", "10-return-gift-notice"],
  "14-reports-gift-discount": ["01-discounts", "02-gifts", "03-reason-cards"],
  "08-pos-consignment": ["01-cart-consignment-item", "02-cash-change", "03-invoice-off-hint", "04-complete"],
  "09-sales": ["01-list", "02-signature-evidence", "03-push-ack", "03b-kiosk-ack-task", "03c-kiosk-ack-done", "04-return-dialog", "05-return-filled", "06-return-done", "07-void-dialog", "08-void-done"],
  "09-invoice-return-void": ["02-return-dialog-invoiced", "03-after-return", "04-void-dialog-invoiced", "05-after-void"],
  "09-invoice-disposition": ["01-void-notice", "02-paper-checked-still-blocked", "03-kiosk-consent", "04-kiosk-signed", "05-ready-to-submit", "06-return-done", "07-allowance-partial", "08-allowance-cross-month", "09-void-carrier-no-paper", "10-taiwanpay-three-confirmations"],
  "09-sales-void": ["01-void-dialog", "02-void-done"],
  "10-consignment": ["01-pending-list", "02-drawer-status", "03-tabs", "04-search-by-phone", "05-pay-dialog", "06-paid", "07-paid-tab", "08-cancelled-tab"],
  "11-purchasing": ["01-po-tab-empty", "02-low-stock", "03-supplier-tab", "04-supplier-create-form", "05-supplier-created", "06-supplier-edit", "07-supplier-search", "08-po-create-empty", "09-po-product-search", "10-po-lines", "11-po-created", "12-po-filter-pending", "13-po-detail", "14-po-receive-dialog", "15-po-receive-invoice", "16-po-received", "17-catalog-after-receive"],
  "12-stocktake": ["01-list-empty", "02-draft-detail", "03-counted", "04-confirm-dialog", "05-confirmed", "06-list-after", "07-catalog-after-stocktake"],
  "13-signing": ["01-list", "02-filters", "03-filter-kind", "04-filter-all", "05-evidence"],
  "14-reports": ["02-dashboard", "03-insights", "04-trends", "05-daily-cash", "06-sales-margin", "07-campaign-performance", "08-inventory-value", "09-consignment-payables", "10-liability", "11-flows", "12-effectiveness", "13-reconciliation", "14-export-buttons", "15-date-changed"],
  "15-settings": ["01-page", "02-general-card", "03-general-saved", "04-mobile-pay-card", "05-premium-card", "01-premium-confirm", "02-premium-saved", "03-premium-history", "04-signature-retention"],
  "16-backup": ["01-backup-503", "02-settings-saved"],
  "17-einvoice": ["01-settings-einvoice-on", "02-settings-linepay-on", "03-pos-invoice-fields", "04-pos-tender-linepay", "05-invoice-taxid-error", "06-invoice-b2b", "07-checkout-invoice-result"],
};

const manifest = [];
const missing = [];
for (const [dir, files] of Object.entries(PICK)) {
  for (const name of files) {
    const file = join(SHOTS_ROOT, dir, `${name}.png`);
    const id = `${dir}/${name}`;
    if (!existsSync(file)) {
      missing.push(id);
      continue;
    }
    manifest.push({ id, file, mtime: statSync(file).mtimeMs, maxWidth: 1000, quality: 0.72 });
  }
}

// 陳舊截圖偵測：有些情境預設會被跳過（例如需 opt-in 的發票開立），此時舊圖仍留在磁碟上，
// 只憑「檔案存在」就收進手冊，會把過期的畫面當成本次驗證結果出貨（QA 只驗圖能顯示，抓不到）。
// 因此以「本批最新截圖時間」為基準，超過門檻的舊圖一律列出並讓產生流程失敗，
// 除非操作者明確以 MANUAL_ALLOW_STALE=true 表示知情。
const STALE_HOURS = Number(process.env.MANUAL_STALE_HOURS ?? 48);
const newest = manifest.reduce((max, item) => Math.max(max, item.mtime), 0);
const stale = manifest
  .filter((item) => newest - item.mtime > STALE_HOURS * 3600_000)
  .map((item) => ({ id: item.id, ageHours: ((newest - item.mtime) / 3600_000).toFixed(1) }));

writeFileSync(
  join(SHOTS_ROOT, "manifest.json"),
  JSON.stringify(
    manifest.map(({ id, file, maxWidth, quality }) => ({ id, file, maxWidth, quality })),
    null,
    2,
  ),
);
console.log(`清單 ${manifest.length} 張；缺檔 ${missing.length}；最新截圖 ${new Date(newest).toISOString()}`);
if (missing.length) console.log(missing.join("\n"));
if (stale.length > 0) {
  console.error(
    `\n⚠ 有 ${stale.length} 張截圖比本批最新的舊超過 ${STALE_HOURS} 小時，可能是被跳過的情境沿用舊圖：`,
  );
  for (const item of stale) console.error(`   ${item.id}（舊 ${item.ageHours} 小時）`);
  if (process.env.MANUAL_ALLOW_STALE !== "true") {
    console.error(
      `\n請重跑對應腳本重新擷取；確認這些舊圖仍正確時，才用 MANUAL_ALLOW_STALE=true 略過本檢查。`,
    );
    process.exit(1);
  }
  console.error("（已由 MANUAL_ALLOW_STALE=true 明確略過）\n");
}
