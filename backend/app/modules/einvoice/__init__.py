"""電子發票（e-invoice）模組：本地發票紀錄 + Turnkey 拋檔外送佇列（T13/T14）。

⚠️ 憑證與 Linux x86-64 主機到位前的 **cert-independent 基礎建設**：資料模型、佇列狀態機、
原子拋檔機制、API 殼皆可離線實作與測試。實際 MIG XML 序列化與 Turnkey 回執解析
（依官方 MIG 4.1 XSD / Turnkey 3.9 手冊）為刻意保留的 seam，待收尾階段補（docs/14 §4、
docs/18）。與 Turnkey 為檔案交換 + 回執輪詢模型，非直接 API 呼叫。
"""
