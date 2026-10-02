// 店內 backend → Worker 的簽章驗證（docs/44 §5.2、§8.1 T7）：
// 沒簽、簽錯、時間差超過 5 分鐘、重放同一個 nonce、body 被改過，一律 401。
import { exports } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { SNAPSHOT, integration, sign } from "./helpers";

const body = JSON.stringify(SNAPSHOT);

describe("POS 整合 API 簽章", () => {
  it("正確簽章可以發佈", async () => {
    const resp = await integration("PUT", "/integration/menu", body);
    expect(resp.status).toBe(200);
  });

  it("沒簽章 401", async () => {
    const resp = await exports.default.fetch(
      new Request("https://order.test/integration/menu", { method: "PUT", body }),
    );
    expect(resp.status).toBe(401);
  });

  it("用錯的密鑰簽 401", async () => {
    const resp = await integration("PUT", "/integration/menu", body, {}, { secret: "wrong" });
    expect(resp.status).toBe(401);
  });

  it("時間差超過 5 分鐘 401", async () => {
    const old = Math.floor(Date.now() / 1000) - 301;
    const resp = await integration("PUT", "/integration/menu", body, {}, { timestamp: old });
    expect(resp.status).toBe(401);
  });

  it("同一個 nonce 用第二次 401（防重放）", async () => {
    const nonce = crypto.randomUUID();
    expect((await integration("PUT", "/integration/menu", body, {}, { nonce })).status).toBe(200);
    expect((await integration("PUT", "/integration/menu", body, {}, { nonce })).status).toBe(401);
  });

  it("簽的是別的 body 401（防竄改）", async () => {
    const headers = await sign("PUT", "/integration/menu", body);
    const tampered = JSON.stringify({ ...SNAPSHOT, version: 99 });
    const resp = await exports.default.fetch(
      new Request("https://order.test/integration/menu", { method: "PUT", headers, body: tampered }),
    );
    expect(resp.status).toBe(401);
  });

  it("簽的是別的路徑 401", async () => {
    const headers = await sign("PUT", "/integration/tables", body);
    const resp = await exports.default.fetch(
      new Request("https://order.test/integration/menu", { method: "PUT", headers, body }),
    );
    expect(resp.status).toBe(401);
  });

  it("回應不透露原因細節（不教攻擊者哪裡錯）", async () => {
    const resp = await integration("PUT", "/integration/menu", body, {}, { secret: "wrong" });
    expect(await resp.json()).toEqual({ error: "unauthorized" });
  });
});
