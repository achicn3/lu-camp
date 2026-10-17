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
      carrier: null,
    });
  });

  // 客人 LINE Pay 綁定的手機條碼載具（店主 2026-10-17；已向 LINE Pay 開通 merchantReference）。
  // 規則與店內 Offline 收款相同（backend sales/linepay.py `_mobile_carrier`）：只認 MOBILE_CARRIER、
  // cardId 帶不帶 / 都收並補上 /、不成形的當沒有。
  it.each([
    ["帶斜線", [{ cardType: "MOBILE_CARRIER", cardId: "/ABC+123" }], "/ABC+123"],
    ["不帶斜線", [{ cardType: "MOBILE_CARRIER", cardId: "ABC+123" }], "/ABC+123"],
    ["前後空白", [{ cardType: "MOBILE_CARRIER", cardId: " /A1B2.C3 " }], "/A1B2.C3"],
    ["別的卡型略過", [{ cardType: "MEMBER", cardId: "M001" }, { cardType: "MOBILE_CARRIER", cardId: "/XYZ-999" }], "/XYZ-999"],
    ["小寫不收", [{ cardType: "MOBILE_CARRIER", cardId: "/abc+123" }], null],
    ["長度不對不收", [{ cardType: "MOBILE_CARRIER", cardId: "/ABC123" }], null],
    ["不是字串不收", [{ cardType: "MOBILE_CARRIER", cardId: 1234567 }], null],
  ])("請款回應帶回綁定載具：%s", (_, cards, expected) => {
    const text = JSON.stringify({ returnCode: "0000", returnMessage: "Success.", info: { merchantReference: { affiliateCards: cards } } });
    expect(parseLinePay(text).carrier).toBe(expected);
  });

  it("沒有 merchantReference（沒綁或沒開通）：載具為 null", () => {
    expect(parseLinePay('{"returnCode":"0000","returnMessage":"Success.","info":{}}').carrier).toBeNull();
    expect(parseLinePay('{"returnCode":"0000","info":{"merchantReference":{"affiliateCards":"x"}}}').carrier).toBeNull();
  });

  it("沒有交易號或壞掉的回應：交易號為 null、壞 JSON 當成未知", () => {
    expect(parseLinePay('{"returnCode":"1150","returnMessage":"no transaction"}')).toMatchObject({
      code: "1150", transactionId: null,
    });
    expect(parseLinePay("<html>")).toMatchObject({ code: null });
  });
});
