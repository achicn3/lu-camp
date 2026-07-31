// 產生要內嵌的截圖清單（id → 檔案），供 convert-images.mjs 使用。
import { existsSync, writeFileSync } from "node:fs";
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
  "08-pos-consignment": ["01-cart-consignment-item", "02-cash-change", "03-invoice-off-hint", "04-complete"],
  "09-sales": ["01-list", "02-signature-evidence", "03-push-ack", "03b-kiosk-ack-task", "03c-kiosk-ack-done", "04-return-dialog", "05-return-filled", "06-return-done", "07-void-dialog", "08-void-done"],
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
    manifest.push({ id, file, maxWidth: 1000, quality: 0.72 });
  }
}
writeFileSync(join(SHOTS_ROOT, "manifest.json"), JSON.stringify(manifest, null, 2));
console.log(`清單 ${manifest.length} 張；缺檔 ${missing.length}`);
if (missing.length) console.log(missing.join("\n"));
