# ADR-008：PII 去重以 HMAC blind index（national_id 不可明文搜尋）

- **Status**: Accepted
- **Context**: 收購/寄售強制蒐集 `national_id`（高敏個資，欄位層級加密，見 ADR-005）。加密後無法以明文索引/比對，但業務需求要「以身分證號比對既有賣方避免重複建檔」。
- **Decision**:
  - 除加密欄 `national_id_enc` 外，另存 `national_id_blind_index = HMAC(national_id, 金鑰)` 獨立索引欄，**僅供精確去重比對**（建檔/收購流程查既有 contact）。
  - **不可明文/部分搜尋** national_id；一般 `?q=` 只比對姓名/電話。提供 `GET /contacts/lookup?national_id=` 由後端雜湊後精確比對。
  - HMAC 金鑰與加密金鑰一樣由環境/KMS 管理、不入 repo。
- **Alternatives**: 明文索引（外洩風險高，違反 ADR-005）；不可去重（會重複建檔、來源登記混亂）；可逆確定性加密當索引（金鑰外洩即可解，安全性弱於 HMAC 單向）。
- **Consequences**: ＋可精確去重又不暴露明文、可建唯一索引；－只能精確比對（不支援模糊/部分比對），金鑰輪替需重算 blind index。
- **Trade-off**: 以「單向 HMAC 精確比對」換取「去重能力 vs 明文保護」的平衡。
