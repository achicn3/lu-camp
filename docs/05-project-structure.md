# 05 — 專案結構

**此結構為強制規範。Claude Code 不得擅自更動模組劃分與分層方向；如需偏離先詢問。**

## Monorepo 佈局

**單一 repo、根目錄即本 repo 根 `lu-camp/`（不另建 `store-system/` 子資料夾）。前端、後端、硬體代理為並列的最上層資料夾，禁止拆成不同 repo。** 所有程式碼都建立在此樹內：
```
lu-camp/                          # = 本 repo 根
├── CLAUDE.md
├── docker-compose.yml
├── docs/
│   ├── (本文件包)
│   └── adr/                      # 後續新 ADR
├── media/                        # 商品照片等檔案儲存(卷掛載, 納入備份; DB 只存相對路徑)
├── backend/
├── frontend/
└── hardware-agent/
```

## 後端（FastAPI 模組化單體）

```
backend/
├── pyproject.toml
├── alembic.ini
├── alembic/
│   └── versions/
├── app/
│   ├── main.py                  # app factory, router 掛載
│   ├── core/
│   │   ├── config.py            # 設定(env), 不含祕密明文
│   │   ├── db.py                # engine/session, async
│   │   ├── security.py          # 密碼雜湊, JWT
│   │   ├── crypto.py            # PII 欄位加密/解密 + national_id HMAC blind-index helper
│   │   ├── audit.py             # audit_log 寫入 helper
│   │   ├── money.py             # Decimal 工具(NT$整數元): round_ntd / split_tax_inclusive / commission / 定價(margin)
│   │   └── deps.py              # 共用依賴(目前使用者, 角色檢查, store 範圍)
│   ├── modules/
│   │   ├── auth/
│   │   │   ├── router.py
│   │   │   ├── service.py
│   │   │   ├── repository.py
│   │   │   ├── models.py        # SQLAlchemy
│   │   │   ├── schemas.py       # Pydantic
│   │   │   └── tests/
│   │   ├── contacts/
│   │   ├── inventory/           # 含 brand / product_model 主檔與定價輔助(用 core/money)
│   │   ├── acquisition/
│   │   ├── consignment/
│   │   ├── purchasing/
│   │   ├── sales/
│   │   ├── einvoice/
│   │   ├── returns/
│   │   ├── cashdrawer/
│   │   ├── stocktake/
│   │   ├── reporting/
│   │   ├── settings/
│   │   └── notification/        # 預留, no-op 實作
│   └── shared/
│       ├── enums.py
│       ├── exceptions.py
│       └── pagination.py
└── tests/
    ├── conftest.py              # 測試 DB(容器/交易回滾), fixtures, factories
    ├── integration/
    └── e2e/
```

### 分層規則（強制）
- `router` → `service` → `repository` → `models`。
- `router`：解析/驗證請求（schemas）、呼叫 service、組回應；**無業務邏輯**。
- `service`：業務邏輯、不變量、交易邊界；唯一能協調多 repository / 跨模組 service。
- `repository`：唯一直接使用 ORM/SQL 的層；回傳領域物件或 DTO。
- 跨模組：只 import 對方 `service`，**禁止** import 對方 `repository`/`models`。
- 金額一律 `Decimal`（用 `core/money.py`）。

### 每個模組的測試
- 模組內 `tests/`：service 單元測試（邏輯/不變量）。
- 全域 `tests/integration`：API + DB。
- 全域 `tests/e2e`：跨模組流程。

## 前端（Next.js App Router）

```
frontend/
├── package.json
├── tsconfig.json                # strict
├── app/
│   ├── (auth)/login/
│   ├── pos/                     # 結帳
│   ├── acquisition/             # 收購鑑價入庫
│   ├── inventory/               # 庫存(序號品/數量品)
│   ├── consignment/             # 寄售結算/應付
│   ├── contacts/                # 會員/賣方
│   ├── purchasing/              # 供應商/採購
│   ├── cash/                    # 開帳/結帳/對帳
│   ├── stocktake/
│   ├── reports/
│   └── settings/
├── components/
│   ├── ui/                      # 基礎元件
│   └── domain/                  # 業務元件(購物車, 鑑價表單...)
├── lib/
│   ├── api.ts                   # 後端 API client(型別來自 schema)
│   ├── hardware.ts              # localhost 硬體代理 client
│   ├── money.ts                 # 金額顯示/解析(不用 float)
│   └── auth.ts
├── hooks/
└── __tests__/                   # 元件/邏輯測試; e2e 用 Playwright(放 e2e/)
```

> 本檔僅規範前端「結構」。畫面、流程、資料策略、硬體整合、角色差異與前端測試見 `docs/10-frontend-spec.md`。

## 硬體代理

```
hardware-agent/
├── pyproject.toml
├── agent/
│   ├── main.py                  # FastAPI/Flask, 綁 localhost
│   ├── escpos_printer.py        # 收據/證明聯/標籤
│   ├── cash_drawer.py           # 透過印表機 kick 開櫃
│   └── config.py                # 印表機型號/連接設定
└── tests/
```

## docker-compose（概念）

```
services:
  db:        postgres:16  (volume 持久化)
  backend:   build ./backend  (depends_on db, 跑 alembic upgrade 後啟動)
  frontend:  build ./frontend
  # hardware-agent 通常部署在 POS 實機(需接印表機), 可獨立執行
  # backup:  排程 pg_dump -> 本地 + 雲端
```