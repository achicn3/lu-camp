# 15 — 裝置狀態 SDK 能力對照（G2 閘門研究成果）

> 本檔為 **Phase 3 裝置狀態面板 B 級（T17/T18）動工前置閘門 G2** 的查證成果，供 hardware-agent 實作引用（見 ADR-010、ADR-011、docs/01 N-1、docs/04 `/devices/status`）。
> 原則：依各機型**實際**能查到的狀態實作；報不到者標 `unsupported`，**不臆造、不當故障**。查證日：2026-06。
>
> **更新（2026-06-07，連線方式與範圍裁示，見 ADR-011）**：
> - **兩台裝置皆為網路連線**：EPSON TM-T82III 為**網路版（Ethernet/LAN，IP 由設定）**、Brother QL-810W 亦為網路（Wi-Fi）；皆非 USB。
> - **A 級統一以 TCP 9100 連線探測 + 心跳**判定在線/離線（連得上＝在線；不依賴 ESC/POS DLE EOT 狀態回應，避免「連得上但未回狀態」被誤判離線）。兩台共用同一 `_tcp_probe`。
> - **EPSON B 級（缺紙/上蓋/錯誤/錢櫃開關偵測）改為不做**（產品裁示）：這類狀態機器本身會以燈號表現、店員現場肉眼可見，面板不重複偵測；對應鍵一律標 `unsupported`。錢櫃**「彈開指令」仍要做**（屬列印/drawer 功能，經 EPSON drawer port，結帳必需），但錢櫃**「開/關狀態偵測」不做**。
> - 本次更新使**面板專注 A 級**：網路裝置掉線店員肉眼看不出，連線/離線+心跳才是面板核心價值。
> - IP **不寫死於程式碼**，一律由環境變數提供（`AGENT_EPSON_HOST`/`AGENT_BROTHER_HOST`…，見 `hardware-agent/.env.example`、agent/config.py）。

## 0. 關鍵發現（閘門擋下的「憑記憶假設」陷阱）

**兩家原廠皆無第一線跨平台 Python SDK：**
- **Brother**：官方 SDK 為 **b-PAC（僅 Windows，COM）** 與行動版（iOS/Android）Print SDK；無 Linux/Python 狀態 SDK。跨平台 Python 實務上用社群 **`brother_ql`**（光柵協定）。
- **EPSON**：官方 **Epson ePOS SDK**（Android/iOS/JavaScript/Java）；**ePOS-Device SDK 已停止維護**；無官方 Python SDK。Python 實務上用社群 **`python-escpos`**（實作 ESC/POS DLE EOT 即時狀態）。

⇒ hardware-agent（Python/Linux）做狀態查詢，靠**社群庫 + 原廠協定文件**（Brother 光柵指令參考 / EPSON ESC/POS 指令參考），而非原廠 Python SDK。

## 1. 既定範圍（裁示 2026-06；連線/範圍更新 2026-06-07，見 ADR-011）

| 機型（**皆網路連線**） | A 級（連線/離線+心跳） | B 級（缺紙/上蓋/錯誤/錢櫃開關） |
|---|---|---|
| **Brother QL-810W**（Wi-Fi） | ✅ 做（TCP 9100 探測） | ❌ **標 `unsupported`** |
| **EPSON TM-T82III**（Ethernet/LAN） | ✅ 做（TCP 9100 探測） | ❌ **標 `unsupported`**（產品裁示不做） |
| 掃碼槍（HID） | ✅（依附主機推定） | n/a |
| 錢櫃（接 EPSON drawer port） | ✅（依附 EPSON 推定） | ❌ **開/關狀態偵測不做、標 `unsupported`**；惟**彈開指令要做**（列印/drawer 功能） |

**EPSON B 級改不做之理由（2026-06-07 裁示）**：缺紙/上蓋這類狀態機器本身會亮燈、店員現場肉眼可見，系統面板不重複偵測（靠機器 + 店員現場即可）；網路裝置「掉線」店員肉眼看不出，**A 級（連線/離線+心跳）才是面板核心價值**。技術上 `python-escpos` 網路後端**能**讀回 DLE EOT 即時狀態（見 §3），但依產品決定不做、連功能帶測試都不做。

**錢櫃**：彈開「指令」屬列印/drawer 功能（結帳必需），經 EPSON drawer port 以 `cashdraw()`／ESC `p` 位元組**經網路送達**，要做且要有測試；錢櫃「開/關狀態偵測」屬 B 級，省、標 `unsupported`。

**Brother B 級不做之理由**：`brother_ql` 網路後端不支援讀回狀態；無線是此機賣點、缺標籤店員肉眼可見、SNMP 複雜度不划算。A 級照做、B 級誠實標不支援。

> 原則不變：**要做的功能就要有測試、不做就整個不做**，不出現「做了功能卻不寫測試」。

## 2. Brother QL-810W（標籤機，網路／Wi-Fi）

| 項目 | 能否查得 | 依據 / 路徑 | hardware-agent 結論 |
|---|---|---|---|
| **A：在線/離線 + 最後回應** | ✅ | TCP 9100 連線探測 / socket 心跳（與 EPSON 共用 `_tcp_probe`；SNMP 亦可，但本案不採） | A 級做（網路離線偵測 + 心跳時間戳） |
| B：缺紙 / 無標籤捲 | ⚠️ 技術受限 | `brother_ql` **網路後端不支援讀回狀態**（官方 README 明載 *wrong label type* / *end of label roll* 無法偵測）；僅 USB 後端有 32-byte 狀態回應 | Wi-Fi 下 **`unsupported`** |
| B：上蓋開啟 | ⚠️ 技術受限 | 同上（USB 狀態回應含 phase/error 位元） | **`unsupported`** |
| B：印表機錯誤 | ⚠️ 技術受限 | SNMP printer-MIB 可取部分（OID `1.3.6.1.4.1.2435.…`），本案不採 | **`unsupported`** |

> 未來若需 Brother B 級：可改 **USB** 連線（用光柵狀態回應）或啟用 **SNMP**（`pysnmp` + Brother MIB）。屆時再評估，現階段不做。

## 3. EPSON TM-T82III（收據/發票機，**網路版**，錢櫃接其 drawer port）

以 `python-escpos` **3.1** 實測 API 為準（連線後端 `escpos.printer.Network(host, port=9100, timeout=…)`，底層 TCP；狀態走 ESC/POS DLE EOT）：

| 項目 | 技術上能否查得 | 依據 / API | hardware-agent 結論（產品裁示） |
|---|---|---|---|
| **A：在線/離線 + 最後回應** | ✅ | **TCP 9100 連線探測 + 心跳**（連得上＝在線；不依賴 DLE EOT 回應，避免「連得上但未回狀態」誤判離線） | **A 級做** |
| B：缺紙 / 將盡 | ✅（技術可行） | `paper_status()`／`query_status()` 經 `Network._read()`（TCP `recv`）讀 DLE EOT n=4 | ❌ **不做、標 `unsupported`**（機器亮燈+店員肉眼，面板不做） |
| B：上蓋開啟 | ✅（技術可行，需解析） | DLE EOT n=2 原始 byte | ❌ **不做、標 `unsupported`** |
| B：印表機錯誤（切刀/機構/可回復） | ✅（技術可行，需解析） | DLE EOT n=3 原始 byte | ❌ **不做、標 `unsupported`** |
| B：**錢櫃開啟狀態** | ✅（技術可行，需解析） | DLE EOT n=1 含 drawer kick connector pin 3 位準 | ❌ **狀態偵測不做、標 `unsupported`** |
| 動作：**彈開錢櫃** | ✅ | `cashdraw(pin)`／ESC `p` 位元組 → `Network._raw()`（TCP `sendall`）**經網路送達** | **要做**（結帳必需；屬列印/drawer 功能，非狀態查詢） |

> **連線/錯誤語意（網路版）**：`Network` 建構即 `connect`，連線失敗拋原生 `OSError`（`ConnectionRefusedError`／`TimeoutError`／不可達／DNS），**無 escpos 專屬包裝**。誠實原則：可辨識的連不上＝合理離線（`probe_error=None`）；其他非預期例外＝`online=False` 但 `probe_error` 如實記，**不偽裝成單純離線**。原 USB 版的 `DeviceNotFoundError`/`USBError`/udev 權限區分**已不適用、移除**。
> **技術可行但不做**：DLE EOT 狀態經網路（`recv`）讀得回來，但 B 級依產品裁示一律不做、標 `unsupported`（ADR-011）。

## 4. hardware-agent `/devices/status` 回傳對應（落地指引）

對照 docs/04 之 `GET /devices/status`：

```jsonc
{ "devices": [
  { "id": "brother-1", "kind": "LABEL_PRINTER", "model": "Brother QL-810W",
    "online": true, "last_seen": "2026-06-07T08:00:00Z",
    "details": {},                                  // A 級為主
    "unsupported": ["paper_out", "cover_open", "error"],   // 網路下 B 級不做
    "driver": "real", "validated_on_hardware": false, "probe_error": null },
  { "id": "epson-1", "kind": "RECEIPT_PRINTER", "model": "EPSON TM-T82III",
    "online": true, "last_seen": "2026-06-07T08:00:00Z",
    "details": {},                                  // A 級為主（B 級不做）
    "unsupported": ["paper_out", "cover_open", "error"],   // 產品裁示不做 → unsupported
    "driver": "real", "validated_on_hardware": false, "probe_error": null },
  { "id": "drawer-1", "kind": "CASH_DRAWER", "model": "EPSON drawer port",
    "online": true, "last_seen": "2026-06-07T08:00:00Z",
    "details": {},
    "unsupported": ["drawer_open"],                 // 開/關狀態偵測不做（彈開指令另做）
    "driver": "real", "validated_on_hardware": false, "probe_error": null }
] }
```

- `unsupported` 內的鍵代表「該機型不偵測/查不到」，前端顯示「此項不支援」，**不得當成故障**（ADR-010）。EPSON 改網路 + B 級裁示不做後，與 Brother 對齊成「**只回 A 級**」。
- `probe_error`：探測時遇到的**驅動/設定錯誤**（非單純離線）的如實描述；前端應顯示錯誤、不誤導店員「只是離線」。
- 代理本身離線（輪詢失敗）為另一態，由前端面板處理（docs/10）。

## 5. T18 動工前仍須完成（閘門未解項）

> 範圍更新後（EPSON 改網路、兩台 A 級、B 級不做）：原「解析 DLE EOT 位元、實測 paper/cover/drawer」等 **B 級項目全部移除**。

1. **實機到貨後**確認兩台網路 IP/port（建議路由器 DHCP 綁 MAC 固定），填入 `.env`（`AGENT_EPSON_HOST`／`AGENT_BROTHER_HOST`）。
2. 在實機上驗證 `_tcp_probe` 對兩台的在線/離線判定與心跳，將 `validated_on_hardware` 改 `true`（T18）。
3. 驗證彈錢櫃指令（`cashdraw()`／ESC `p`）經 EPSON 網路後端實際踢開錢櫃。
4. A 級狀態目前以單元測試（mock socket）守住；實機驗證後通知使用者。

## 來源

- `brother_ql` README（網路後端不可讀狀態）：https://github.com/pklaus/brother_ql/blob/master/README.md
- Brother Desktop/Mobile SDK 下載：https://developerprogram.brother-usa.com/sdk-download
- Brother b-PAC 運作環境（Windows）：https://support.brother.com/g/s/es/dev/en/bpac/environment/index.html
- Brother SNMP 設定：https://help.brother-usa.com/app/answers/detail/a_id/164663/
- `python-escpos` methods（`is_online`/`paper_status`/`query_status`/`cashdraw`）：https://python-escpos.readthedocs.io/en/latest/user/methods.html
- `python-escpos` 印表機後端（`Network(host, port=9100)`／`Usb`…）：https://python-escpos.readthedocs.io/en/latest/user/printers.html
- EPSON ESC/POS DLE EOT 即時狀態參考：https://download4.epson.biz/sec_pubs/pos/reference_en/escpos/dle_eot.html
- EPSON TM-T82III 支援指令：https://download4.epson.biz/sec_pubs/pos/reference_en/escpos/tmt82iii.html
- Epson ePOS SDK 支援機種：https://download4.epson.biz/sec_pubs/pos/reference_en/epos_and/ref_epos_sdk_and_en_devicespecifications_listofsupportedapis.html
