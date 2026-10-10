# LuCamp 文件入口

本目錄保存產品需求、架構、資料模型、API、測試與交付流程。開始實作前，先依工作範圍閱讀對應文件；若文件與程式行為不一致，先記錄差異，不要假設其中任一方正確。

## 所有工作都要先讀

- [產品需求](./01-requirements.md)
- [系統架構](./02-architecture.md)
- [資料模型](./03-data-model.md)
- [API 規格](./04-api-spec.md)
- [專案結構](./05-project-structure.md)
- [TDD 策略](./06-tdd-strategy.md)
- [開發路線圖](./07-roadmap.md)
- [開發工作流](./08-workflow.md)
- [Git／worktree 工作流](./12-git-workflow.md)
- [目前狀態](./current-status.md)
- [延後項目](./deferred-items.md)

## 前端與 API contract

- [Frontend 規格](./10-frontend-spec.md)
- [API contract](./11-api-contract.md)
- [Browser E2E smoke](./20-browser-e2e-smoke.md)

## 功能與整合規格

- [電子發票 Turnkey/MIG 歷史研究](./14-einvoice-mig-mapping.md)
- [裝置 SDK 能力](./15-device-sdk-capability.md)
- [Store credit](./16-store-credit.md)
- [會員中心](./17-member-center.md)
- [報表與風險 review](./19-reports-and-risk-review-plan.md)
- [門市活動](./21-store-campaigns-plan.md)
- [門市活動 v2：多活動、指定商品、疊加、買 N 送 M、組合包](./40-promotions-v2.md)
- [備份與還原](./22-backup-restore.md)
- [Kiosk 簽署](./23-kiosk-signing-spec.md)
- [AMEGO 電子發票](./24-amego-einvoice.md)
- [贈品與臨時折扣](./32-gift-and-manual-discount.md)
- [贈品與臨時折扣：審查交接筆記](./33-gift-discount-handover.md)
- [餐飲內用桌號與出餐單](./35-dine-in-table-and-kitchen-ticket.md)
- [手開紙本發票登記](./36-manual-paper-invoice.md)
- [收購佇列：現場收件估價付款、空檔上架（規格草案）](./42-acquisition-intake-queue.md)
- [MacBook 正式機：讓區網平板也能列印](./41-macbook-lan-tablet-printing.md)
- [MacBook 正式機：升級到 2026-09-26 版（給正式機上的 AI 照做）](./43-macbook-upgrade-2026-09-26.md)
- [線上掃碼點餐（Cloudflare）＋彈性菜單（規格草案）](./44-online-ordering.md)
- [MacBook 正式機：升級到 2026-10-01 版（彈性菜單後端）](./45-macbook-upgrade-2026-10-01-menu-options.md)
- [MacBook 正式機：升級到 2026-10-01 版（餐飲每日限量）](./46-macbook-upgrade-2026-10-01-daily-stock.md)
- [餐飲交易紀錄頁＋餐點部分退款](./47-fnb-transactions-and-refund.md)
- [MacBook 正式機：升級到 2026-10-01 版（餐飲交易紀錄、餐點退款、點選同意）](./48-macbook-upgrade-2026-10-01-fnb-refund.md)
- [餐飲損耗與成本（報廢、客訴退款、選項成本、關帳提醒）](./49-fnb-waste-and-cost.md)
- [線上點餐／彈性菜單：交接文件（現況、上線注意、下一步）](./50-online-ordering-handoff.md)
- [露坑線上菜單體驗與 POS 整合計畫（2026-10-06 店主需求、分期與驗收）](./63-online-menu-experience-plan.md)
- [MacBook 正式機：升級到 2026-10-01 版（餐飲損耗與成本）](./51-macbook-upgrade-2026-10-01-fnb-waste.md)
- [MacBook 正式機：升級到 2026-10-01 版（POS 選項點餐＋菜單選項管理）](./52-macbook-upgrade-menu-options.md)
- [MacBook 正式機：升級到 2026-10-02 版（發票撞號修正）](./53-macbook-upgrade-2026-10-02-einvoice-order-id.md)
- [MacBook 正式機：升級到 2026-10-04 版（POS 結帳：備註末三碼、總件數、完成頁找零）](./59-macbook-upgrade-pos-checkout-notes.md)
- [MacBook 正式機：升級到 2026-10-04 版（排隊收購「詳細」按折數帶出收購價）](./60-macbook-upgrade-intake-detail-cost.md)
- [MacBook 正式機：升級到 2026-10-04 版（組合包袋裝條碼）](./61-macbook-upgrade-bundle-packs.md)
- [MacBook 正式機：升級到 2026-10-08 版（線上點餐：店內收單＋菜單管理改版）](./64-macbook-upgrade-online-orders-store.md)
- [MacBook 正式機：升級到 2026-10-08 版（混合付款的購物金不課稅）](./65-macbook-upgrade-store-credit-allowance.md)
- [MacBook 正式機：升級到 2026-10-08 版（手沖體驗卡＋加購角色）](./66-macbook-upgrade-brew-experience.md)
- [MacBook 正式機：升級到 2026-10-08 版（購物金一律扣掉後開發票）](./67-macbook-upgrade-store-credit-deduct-only.md)
- [MacBook 正式機：升級到 2026-10-08 版（線上點餐「帶回家」零售商品＋交貨）](./68-macbook-upgrade-online-retail.md)
- [MacBook 正式機：升級到 2026-10-08 版（線上點餐 LINE Pay 付款）](./69-macbook-upgrade-online-linepay.md)
- [採購單事後修改＋進項發票獨立登錄（設計）](./70-purchasing-edit-and-input-invoices.md)
- [MacBook 正式機：升級到 2026-10-08 版（採購單可修改＋進項發票）](./71-macbook-upgrade-purchasing-edit.md)
- [MacBook 正式機：升級到 2026-10-09 版（線上單作廢／退貨回報雲端已退款）](./72-macbook-upgrade-online-refund-report.md)
- [MacBook 正式機：升級到 2026-10-09 版（餐飲可用購物金折抵）](./73-macbook-upgrade-store-credit-for-food.md)
- [MacBook 正式機：升級到 2026-10-09 版（待整理：客人不賣了可退回、成色選得到）](./74-macbook-upgrade-intake-return.md)
- [MacBook 正式機：升級到 2026-10-09 版（餐飲品項一個視窗編輯、待整理顯示原價）](./75-macbook-upgrade-menu-edit-retail-price.md)
- [MacBook 正式機：升級到 2026-10-09 版（線上點餐「不知道喝什麼」引導推薦）](./76-macbook-upgrade-online-menu-quiz.md)
- [MacBook 正式機：升級到 2026-10-10 版（線上點餐人氣標籤）](./77-macbook-upgrade-online-popularity.md)
- [MacBook 正式機：升級到 2026-10-10 版（線上點餐直接進完整菜單＋店員推薦＋帶著走）](./78-macbook-upgrade-online-full-menu.md)
- [MacBook 正式機：升級到 2026-10-10 版（收購購物金「改成付現」）](./79-macbook-upgrade-convert-payout-to-cash.md)

## 評估與後續實作

- [v0.0.1 系統實跑與優化評估](./25-system-assessment-v0.0.1.md)
- [v0.0.1 multi-agent 實作與審查計畫](./26-v0.0.1-multi-agent-implementation-plan.md)

## 其他計畫與範本

- [Claude Code kickoff](./09-claude-code-kickoff.md)
- [Product spec template plan](./13-product-spec-template-plan.md)
- [UTM Ubuntu Turnkey／電子發票歷史方案](./18-utm-ubuntu-turnkey-einvoice-plan.md)

架構決策記錄位於 [`adr/`](./adr/)，測試與評估截圖位於 [`screenshots/`](./screenshots/)。
