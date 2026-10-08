// LINE Pay Online API v4 客戶端（docs/44 §4.4、§4.4.1）：簽章與 Offline 相同、transactionId 一律取原始字串。
import { describe, expect, it } from "vitest";

import { lineSignature, parseLinePay } from "../src/linepay";

describe("LINE Pay 客戶端", () => {
  it("簽章＝base64(HMAC-SHA256(secret, secret + path + body + nonce))", async () => {
    // 與店內 backend `sign_auth` 同一組向量（backend/app/modules/sales/linepay.py）
    const sig = await lineSignature("secret", "/v4/payments/request", '{"a":1}', "nonce-1");
    expect(sig).toBe("Cr492/UWZ/8aqQfU+bO1elcUznT1NeYuxaU2XtUSp8s=");
  });

  it("transactionId 從原始文字取字串（19 位超過 JS 安全整數）", () => {
    const text = '{"returnCode":"0000","returnMessage":"Success.","info":{"transactionId":2026100102385323710,' +
      '"paymentUrl":{"web":"https://sandbox-web-pay.line.me/x","app":"line://pay/x"}}}';
    expect(parseLinePay(text)).toEqual({
      code: "0000",
      message: "Success.",
      transactionId: "2026100102385323710",
      paymentUrl: "https://sandbox-web-pay.line.me/x",
    });
  });

  it("沒有交易號或壞掉的回應：交易號為 null、壞 JSON 當成未知", () => {
    expect(parseLinePay('{"returnCode":"1150","returnMessage":"no transaction"}')).toMatchObject({
      code: "1150", transactionId: null,
    });
    expect(parseLinePay("<html>")).toMatchObject({ code: null });
  });
});
