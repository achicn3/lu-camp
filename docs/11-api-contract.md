# 11 — 前後端 API 合約（合約優先，省 token、防漂移）

目的：讓前端**不需要重讀後端原始碼去理解 API**，也不需要手刻型別。後端的 OpenAPI 是唯一機器事實來源，前端從它**自動生成**型別與 client，只使用生成物。這同時省 token（前端任務不必載入後端程式）、防止前後端漂移、且編譯期就擋掉呼叫不存在端點/欄位的幻想。

## 1. 單一事實來源

- **機器合約**：後端 FastAPI 自動產生的 `openapi.json`（`GET /openapi.json`）。
- **人類可讀設計**：`docs/04-api-spec.md`（端點意圖）＋本檔（跨切面約定）。
- 兩者衝突時，以後端實際 OpenAPI 為準；發現與 `04` 不符要回報並對齊，而不是各自猜。

## 2. 生成管線

```
後端端點(含完整 Pydantic schema/response_model/operation_id)
        │  匯出
        ▼
frontend/openapi.json   ← 由後端 /openapi.json 匯出（腳本，納入版控）
        │  pnpm gen:api  (openapi-typescript)
        ▼
frontend/lib/api-types.ts   ← 生成型別（納入版控）
        │  使用
        ▼
frontend/lib/api.ts  ← 以 openapi-fetch 包成型別化 client（手寫一次的薄封裝：附 token、解 error、refresh）
```

- 前端所有 API 呼叫都經 `lib/api.ts` 的型別化 client；**禁止手刻 API 介面型別、禁止反推後端原始碼來「理解」API**。
- 後端端點必須：宣告 `response_model` 與請求 schema、設定清楚的 `operation_id`（生成的 client 方法名才乾淨）、加 `tags`（對應模組）。

## 3. 開發順序（這是省 token 的關鍵）

每個能力**先後端、後前端**：
1. 後端：實作端點 + Pydantic schema（TDD），跑綠。
2. 重新匯出 `openapi.json` 並 `pnpm gen:api` 更新型別。
3. 前端：用更新後的型別化 client 實作畫面（TDD）。

→ 前端任務的 context 只需要：`docs/04`、`docs/10`、`lib/api-types.ts`（生成）。**不必載入後端 modules 原始碼**，自然省 token，也不會「重新理解」API。

## 4. 跨切面約定（型別未必涵蓋，兩端都遵守）

- **錯誤格式**：一律 `{ "error": { "code", "message", "details" } }`；`lib/api.ts` 統一解析。
- **認證**：`Authorization: Bearer <access>`；401 由 client 自動 refresh、失敗導回登入。
- **分頁**：統一回應形狀（如 `{ items, total, page, page_size }`），後端共用 `shared/pagination.py`，前端據此處理。
- **金額**：以字串傳輸（避免 float），**新台幣整數元**（含稅定價）；後端轉 `Decimal`、前端用 `lib/money.ts` 解析/顯示。
- **日期時間**：ISO 8601、UTC 字串。
- **列舉**：狀態列舉（如 invoice_status、serialized_item.status）由 OpenAPI 帶出，前端直接用生成型別，不自行定義。

## 5. CI 防漂移（強制）

- CI 在 backend 變更後**重新生成** `openapi.json` 與 `api-types.ts`，與版控中的檔案比對：**有差異即失敗**，逼迫「改了後端就要更新合約與前端型別」。
- 前端 `tsc --strict` 對著生成型別檢查：呼叫不存在的端點/欄位、型別不符，編譯期直接紅燈。
- 因此「前端呼叫了一個後端沒有的 API」這種幻想，在 CI/編譯期就被擋下。

## 6. 指令（Phase 0 設定，實作後補實際指令）

```bash
# 後端：匯出 OpenAPI（範例）
uv run python -m app.scripts.export_openapi  > frontend/openapi.json

# 前端：生成型別 + client
pnpm add -D openapi-typescript
pnpm add openapi-fetch
pnpm gen:api      # = openapi-typescript openapi.json -o lib/api-types.ts

# CI：生成後比對，有 diff 就 fail
git diff --exit-code frontend/openapi.json frontend/lib/api-types.ts
```

> 結論：後端是 API 的唯一事實來源、前端吃生成物。Claude Code 做前端時只讀 `docs/04`、`docs/10` 與生成型別，不讀後端程式碼，省 token 又不漂移。
