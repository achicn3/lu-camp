// 照片與字型（docs/44 §3.4、§8.1 T10）：只收內容雜湊對得上、檔頭正確的 WebP／WOFF2；
// 公開讀取網址帶雜湊、內容永不改變，所以給長效快取。
import { describe, expect, it } from "vitest";

import { get, integration, sha256Hex, webp, woff2 } from "./helpers";

describe("菜單照片", () => {
  it("上傳 → 再傳一次回 204 → 公開讀取長效快取", async () => {
    const data = webp();
    const hash = await sha256Hex(data);
    expect((await integration("PUT", `/integration/photos/${hash}`, data)).status).toBe(201);
    expect((await integration("PUT", `/integration/photos/${hash}`, data)).status).toBe(204);
    const resp = await get(`/photos/${hash}.webp`);
    expect(resp.status).toBe(200);
    expect(resp.headers.get("Content-Type")).toBe("image/webp");
    expect(resp.headers.get("Cache-Control")).toContain("immutable");
    expect(new Uint8Array(await resp.arrayBuffer())).toEqual(data);
  });

  it("雜湊和內容對不上：422", async () => {
    const hash = await sha256Hex(webp(1));
    expect((await integration("PUT", `/integration/photos/${hash}`, webp())).status).toBe(422);
  });

  it("不是 WebP：422", async () => {
    const data = new TextEncoder().encode("<svg onload=alert(1)>");
    const hash = await sha256Hex(data);
    expect((await integration("PUT", `/integration/photos/${hash}`, data)).status).toBe(422);
  });

  it("超過 2 MB：413", async () => {
    const data = webp(2 * 1024 * 1024);
    const hash = await sha256Hex(data);
    expect((await integration("PUT", `/integration/photos/${hash}`, data)).status).toBe(413);
  });

  it.each(["../../secret", "ABC", "a".repeat(63), `${"a".repeat(64)}.png`])(
    "不合法的照片網址 404：%s",
    async (key) => {
      expect((await get(`/photos/${key}.webp`)).status).toBe(404);
    },
  );

  it("沒上傳過的照片 404", async () => {
    expect((await get(`/photos/${"0".repeat(64)}.webp`)).status).toBe(404);
  });
});

describe("手寫字型子集", () => {
  it("上傳 WOFF2 後公開讀取", async () => {
    const data = woff2();
    const hash = await sha256Hex(data);
    expect((await integration("PUT", `/integration/fonts/${hash}`, data)).status).toBe(201);
    const resp = await get(`/fonts/${hash}.woff2`);
    expect(resp.status).toBe(200);
    expect(resp.headers.get("Content-Type")).toBe("font/woff2");
    expect(resp.headers.get("Cache-Control")).toContain("immutable");
  });

  it("不是 WOFF2：422", async () => {
    const data = webp();
    const hash = await sha256Hex(data);
    expect((await integration("PUT", `/integration/fonts/${hash}`, data)).status).toBe(422);
  });
});
