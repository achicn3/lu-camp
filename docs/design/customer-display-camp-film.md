# 客顯動畫「借一點光」— 第一版

工作分支：`feat/kiosk-camp-film`。獨立 worktree：`/home/test/lu-camp-film`，起點 `origin/main` 的 `b9d0cc5`。舊動畫工作保留在 `/home/test/lu-camp-promo`。

## 美術與劇情

參考：[Claude 分享的西瓜短片](https://x.com/claudeai/status/2102471866635919731)。實際觀看影片後，取其紙張顆粒、細線排線、有限色彩、微觀主角與近遠景切換的方向。角色、道具、故事及繪圖均為原創程式圖形，不打包參考影片或複製其畫面。

48 秒循環：螢火蟲發現未亮的營燈 → 靠近開關 → 燈光展開小營地 → 葉下避雨 → 雨後彩虹 → 夜色星光 → 收回營燈特寫。取消人手開門、直接穿越雲海、吊床與搭帳操作。現代用品包括充電營燈、氣柱帳、折疊椅桌、手沖壺、保冷箱、行動電源。

本版為可互動的美術第一版；細節密度與鏡頭表演仍有精修空間，並非參考影片品質的等同複製。

## 客顯事件

| 狀態 | 視覺行為 |
| --- | --- |
| 待機 | 播放完整故事 |
| 購物車 | 故事時間暫存，營燈縮至標題下方、商品上方 |
| 商品數增加 | 螢火蟲帶葉片飛入；連續增加重啟單一短效果，不累積粒子 |
| 付款完成 | 只有既有 COMPLETED 狀態觸發金色燈光 |
| 文件簽署中 | 隱藏畫布、停止動畫計時 |
| 簽署完成 | 既有完成狀態觸發星光連線，與付款區別 |
| 返回待機 | 同一畫布從先前故事時間繼續 |
| 減少動態／背景分頁 | 靜態插畫／暫停 RAF |

所有交易判定沿用既有 API 狀態；動畫不觸發付款或文件提交。Canvas 為裝飾，金額、明細與完成文字仍用原有 DOM。

## 實作位置

- `frontend/features/customer-display/film/state.ts`：純狀態／時間。
- `draw.ts`：原生 Canvas 2D 插畫與分鏡，靜態圖層快取，DPR 上限 2。
- `controller.ts`：RAF、resize、visibility 生命週期。
- `CampingScene.tsx`：React 客顯接點。
- `frontend/app/kiosk/page.tsx`：付款完成使用獨立 `paid` 模式。

## 預覽與驗證

工作樹根目錄執行 `node frontend/scripts/camp-film-preview.mjs`，開啟 http://localhost:5200 。可拖曳時間軸、跳章節及操作各客顯情境；此預覽不呼叫交易 API。

```
pnpm --dir frontend test
pnpm --dir frontend lint
pnpm --dir frontend typecheck
pnpm --dir frontend build
node frontend/scripts/camp-film-preview-smoke.mjs
SMOKE_BASE=http://localhost:3962 SMOKE_API_BASE=http://127.0.0.1:8962 node frontend/scripts/kiosk-camping-scene-smoke.mjs
```

瀏覽器腳本需本機 Playwright Chromium 相依。客顯煙霧腳本僅供已 seed 的開發資料庫：會建立測試商品、現金銷售與簽署文件，不可指向正式資料。

本輪開發 API 8962 使用本機測試 Postgres；Next 3962 為實際整合，5200 為獨立美術預覽。正式部署應使用門市既有的 API 位址設定。

本輪驗證結果：前端 91 個測試檔、873 個測試全過；ESLint、TypeScript、production build 通過；獨立預覽七分鏡／完成效果／暫停與減少動態通過；真 backend + Postgres 客顯煙霧 18/18 通過。截圖在 `/tmp/camp-film-e2e` 與 `/tmp/camp-film-preview-shots`。尚未合併 main 或部署門市，門市設備上的長時間播放效能仍待實測。
