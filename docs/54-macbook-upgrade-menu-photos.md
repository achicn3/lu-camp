# 54 — MacBook 正式機：升級到 2026-10-02 版（菜單照片）

> **給在 MacBook（店內正式機）上工作的 AI agent：** 這份文件是一項**待你執行的升級任務**。
> docs/45、46、48、51、52、53（電子發票平台編號）若還沒做，**直接做這份即可**（`alembic upgrade head` 會一次補齊
> 全部 migration，含 docs/53 的 `3f2adaec554d`）。但 docs/53 若有 migration 以外的步驟或驗證，仍照它另外做。
> 照 docs/43 的「§2 動手前」（記版本、確認打烊、先備份、工作目錄乾淨）做完，再做本文 §2、§3。
> 任何一步失敗：**停下來、把錯誤原文給店主看、不要自己猜著修**。一定要在**打烊後**做。

## 1. 這次多了什麼

| 變更 | 影響到 | 要做的事 |
|---|---|---|
| 餐飲菜單可上傳照片（JPEG／PNG／WebP／iPhone HEIC，10 MB 內）；後端轉 WebP、縮到 1200、去掉位置等拍攝資訊 | 資料庫＋後端＋前端 | migration `2e52d783ec0e` |
| 後端**新套件** `pillow 12.3.0`、`pillow-heif 1.8.0`（讀 iPhone 照片） | 後端 | **一定要 `uv sync`**，否則後端啟動失敗 |
| POS 餐飲磚顯示照片 | 前端 | 重新 build 前端 |

> 前端沒有新套件。兩個後端套件都有 Apple Silicon 的現成安裝檔，`uv sync` 不需要編譯。
> 照片存在資料庫裡，每晚的備份會一起備到 R2，不用另外處理。

## 2. 升級步驟

照 docs/43 §3 的指令依序做（§3.3 的 `uv sync` 這次**不能跳過**）。`alembic current` 應顯示 **`2e52d783ec0e (head)`**（docs/53 寫的 `3f2adaec554d` 是它的前一支，做完本文件後
顯示 `2e52d783ec0e` 才是對的）。

`uv sync` 之後先確認套件裝好了，回報這行輸出：

```bash
cd backend && /opt/homebrew/bin/uv run python -c "import PIL, pillow_heif; print(PIL.__version__, pillow_heif.__version__)" && cd ..
# 應印出：12.3.0 1.8.0
```

## 3. 驗證（每一項都回報結果）

1. docs/43 §4 開頭的 `curl`／`launchctl` 檢查：三個 `state` 都是 `running`、兩個 http 都是 `200`。
2. 請**店主**打開「餐飲菜單」，品項表最左邊多一欄「照片」；挑一個品項按「上傳照片」，用手機拍的照片試傳一張，
   縮圖會出現。
3. 請**店主**打開「POS 結帳」，剛剛那個品項的磚上有照片。不想留的話回菜單頁按「移除」即可。

## 4. 出問題怎麼退

- **只退程式、不要降資料庫**：上傳過照片後這支 migration 會**拒絕降版**（避免丟掉照片）——這是預期行為，不要強制。
- 只退程式：`git checkout <記下的 commit>`，再照 docs/43 §6 `uv sync`、重 build 前端、`install-launchd.sh`。
