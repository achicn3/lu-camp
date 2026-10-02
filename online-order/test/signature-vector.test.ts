// 跨語言簽章向量：店內 backend（Python）與 Worker 必須算出同一個簽章。
// 同一組數字也寫在 backend/tests/test_onlineorder_signing.py；兩邊任何一邊改了規則，兩邊測試都會紅。
import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { verifyIntegration } from "../src/auth";

const VECTOR = {
  secret: "vector-secret",
  method: "PUT",
  path: "/integration/menu",
  timestamp: 1790900000,
  nonce: "0123456789abcdef-vector",
  body: '{"version":1}',
  signature: "c4b01a296615d5083c69d9cb172099a0b773225f5f7d5d98fe2d2778c7e3a373",
};

describe("跨語言簽章向量", () => {
  it("Worker 接受 backend 規則算出的簽章", async () => {
    const req = new Request(`https://order.test${VECTOR.path}`, {
      method: VECTOR.method,
      headers: {
        "X-LuCamp-Timestamp": String(VECTOR.timestamp),
        "X-LuCamp-Nonce": VECTOR.nonce,
        "X-LuCamp-Signature": VECTOR.signature,
      },
    });
    const ok = await verifyIntegration(
      req,
      new TextEncoder().encode(VECTOR.body),
      { ...env, INTEGRATION_SECRET: VECTOR.secret },
      1,
      VECTOR.timestamp,
    );
    expect(ok).toBe(true);
  });
});
