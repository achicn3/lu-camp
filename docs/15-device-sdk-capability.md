# 15 — 裝置狀態 SDK 能力對照（G2 閘門研究成果）

> 本檔為 **Phase 3 裝置狀態面板 B 級（T17/T18）動工前置閘門 G2** 的查證成果，供 hardware-agent 實作引用（見 ADR-010、docs/01 N-1、docs/04 `/devices/status`）。
> 原則：依各機型**實際**能查到的狀態實作；報不到者標 `unsupported`，**不臆造、不當故障**。查證日：2026-06。

## 0. 關鍵發現（閘門擋下的「憑記憶假設」陷阱）

**兩家原廠皆無第一線跨平台 Python SDK：**
- **Brother**：官方 SDK 為 **b-PAC（僅 Windows，COM）** 與行動版（iOS/Android）Print SDK；無 Linux/Python 狀態 SDK。跨平台 Python 實務上用社群 **`brother_ql`**（光柵協定）。
- **EPSON**：官方 **Epson ePOS SDK**（Android/iOS/JavaScript/Java）；**ePOS-Device SDK 已停止維護**；無官方 Python SDK。Python 實務上用社群 **`python-escpos`**（實作 ESC/POS DLE EOT 即時狀態）。

⇒ hardware-agent（Python/Linux）做狀態查詢，靠**社群庫 + 原廠協定文件**（Brother 光柵指令參考 / EPSON ESC/POS 指令參考），而非原廠 Python SDK。

## 1. 既定範圍（裁示 2026-06）

| 機型 | A 級（連線/離線+心跳） | B 級（缺紙/上蓋/錯誤/錢櫃） |
|---|---|---|
| **Brother QL-810W**（維持 Wi-Fi） | ✅ 做 | ❌ **標 `unsupported`** |
| **EPSON TM-T82iii** | ✅ 做 | ✅ 做 |
| 掃碼槍（HID） | ✅（依附主機推定） | n/a |
| 錢櫃（接 EPSON drawer port） | ✅（依附 EPSON 推定） | ✅ 由 EPSON 解析 pin 3 |

**Brother B 級不做之理由**：`brother_ql` 網路後端不支援讀回狀態；無線是此機賣點、缺紙店員肉眼可見、SNMP 複雜度不划算。維持 Wi-Fi、A 級照做、B 級誠實標不支援。

## 2. Brother QL-810W（標籤機，Wi-Fi）

| 項目 | 能否查得 | 依據 / 路徑 | hardware-agent 結論 |
|---|---|---|---|
| **A：在線/離線 + 最後回應** | ✅ | TCP 連線探測 / socket 心跳（SNMP 亦可，但本案不採） | A 級做（Wi-Fi 離線偵測 + 心跳時間戳） |
| B：缺紙 / 無標籤捲 | ⚠️ 技術受限 | `brother_ql` **網路後端不支援讀回狀態**（官方 README 明載 *wrong label type* / *end of label roll* 無法偵測）；僅 USB 後端有 32-byte 狀態回應 | Wi-Fi 下 **`unsupported`** |
| B：上蓋開啟 | ⚠️ 技術受限 | 同上（USB 狀態回應含 phase/error 位元） | **`unsupported`** |
| B：印表機錯誤 | ⚠️ 技術受限 | SNMP printer-MIB 可取部分（OID `1.3.6.1.4.1.2435.…`），本案不採 | **`unsupported`** |

> 未來若需 Brother B 級：可改 **USB** 連線（用光柵狀態回應）或啟用 **SNMP**（`pysnmp` + Brother MIB）。屆時再評估，現階段不做。

## 3. EPSON TM-T82iii（收據/發票機，錢櫃接其 drawer port）

以 `python-escpos` 實測 API 為準（底層為 ESC/POS DLE EOT 即時狀態）：

| 項目 | 能否查得 | 依據 / API | hardware-agent 結論 |
|---|---|---|---|
| **A：在線/離線 + 最後回應** | ✅ | `is_online()`（DLE EOT 即時狀態）+ 心跳 | A 級做 |
| B：缺紙 / 將盡 | ✅ | `paper_status()` → `2`=足量 / `1`=將盡 / `0`=無紙 | **三態現成可做** |
| B：上蓋開啟 | ⚠️ 需解析 | DLE EOT n=2（offline status）cover-open 位元；`query_status()` 取原始 byte 自行解碼（無現成具名方法） | 做，需解析原始 byte |
| B：印表機錯誤（切刀/機構/可回復） | ⚠️ 需解析 | DLE EOT n=3（error status）位元；`query_status()` 原始 byte | 做，需解析 |
| B：**錢櫃開啟狀態** | ⚠️ 需解析 | DLE EOT n=1（printer status）含 **drawer kick connector pin 3** 位準；`cashdraw()` 僅負責**踢開**（輸出），讀狀態須解析原始 byte | 做（讀 pin 3 位準）；非具名方法 |

> 注意：`cashdraw(pin)` 是**輸出**（踢開錢櫃），不是狀態查詢。錢櫃「開/關」狀態須讀 DLE EOT n=1 原始 byte 之 pin 3 位準。

## 4. hardware-agent `/devices/status` 回傳對應（落地指引）

對照 docs/04 之 `GET /devices/status`：

```jsonc
{ "devices": [
  { "id": "...", "kind": "LABEL_PRINTER", "model": "Brother QL-810W",
    "online": true, "last_seen": "2026-06-05T08:00:00Z",
    "details": {},                                  // A 級為主
    "unsupported": ["paper_out", "cover_open", "error"] },   // Wi-Fi 下 B 級不支援
  { "id": "...", "kind": "RECEIPT_PRINTER", "model": "EPSON TM-T82iii",
    "online": true, "last_seen": "2026-06-05T08:00:00Z",
    "details": { "paper": "adequate|ending|empty", "cover_open": false,
                 "error": false, "drawer_open": false },
    "unsupported": [] }
] }
```

- `unsupported` 內的鍵代表「該機型查不到」，前端顯示「此機型不支援」，**不得當成故障**（ADR-010）。
- 代理本身離線（輪詢失敗）為另一態，由前端面板處理（docs/10）。

## 5. T17/T18 動工前仍須完成（閘門未解項）

1. 確認 `python-escpos` 連線方式（USB/網路/serial）與 TM-T82iii 相容性，實測 `is_online()`/`paper_status()`/`query_status()` 回傳。
2. 對 EPSON 解析 DLE EOT n=1/2/3 原始 byte 的**位元定義**（cover/error/drawer pin3），以官方 ESC/POS DLE EOT 參考為準，寫成解析測試。
3. Brother 確認 Wi-Fi 連線探測 + 心跳實作；B 級鍵一律列入 `unsupported`。
4. T18 實機驅動依此（Brother Wi-Fi 連線探測；EPSON ESC/POS）實作，機器到貨後完成通知使用者接上驗證。

## 來源

- `brother_ql` README（網路後端不可讀狀態）：https://github.com/pklaus/brother_ql/blob/master/README.md
- Brother Desktop/Mobile SDK 下載：https://developerprogram.brother-usa.com/sdk-download
- Brother b-PAC 運作環境（Windows）：https://support.brother.com/g/s/es/dev/en/bpac/environment/index.html
- Brother SNMP 設定：https://help.brother-usa.com/app/answers/detail/a_id/164663/
- `python-escpos` methods（`is_online`/`paper_status`/`query_status`/`cashdraw`）：https://python-escpos.readthedocs.io/en/latest/user/methods.html
- EPSON ESC/POS DLE EOT 即時狀態參考：https://download4.epson.biz/sec_pubs/pos/reference_en/escpos/dle_eot.html
- EPSON TM-T82III 支援指令：https://download4.epson.biz/sec_pubs/pos/reference_en/escpos/tmt82iii.html
- Epson ePOS SDK 支援機種：https://download4.epson.biz/sec_pubs/pos/reference_en/epos_and/ref_epos_sdk_and_en_devicespecifications_listofsupportedapis.html
