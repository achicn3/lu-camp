// 安全標頭（docs/44 §8.1 T8）：嚴格 CSP、禁止被嵌入、不猜型別、不送 Referer。
import { describe, expect, it } from "vitest";

import { get } from "./helpers";

describe("安全標頭", () => {
  it.each(["/api/menu", "/api/tables/nope", `/photos/${"0".repeat(64)}.webp`, "/no-such-route"])(
    "%s 帶齊安全標頭",
    async (path) => {
      const resp = await get(path);
      expect(resp.headers.get("Content-Security-Policy")).toContain("default-src 'self'");
      expect(resp.headers.get("Content-Security-Policy")).toContain("frame-ancestors 'none'");
      expect(resp.headers.get("X-Frame-Options")).toBe("DENY");
      expect(resp.headers.get("X-Content-Type-Options")).toBe("nosniff");
      expect(resp.headers.get("Referrer-Policy")).toBe("no-referrer");
      expect(resp.headers.get("Strict-Transport-Security")).toContain("max-age=");
    },
  );

  it("沒有的 API 路徑 404 JSON", async () => {
    const resp = await get("/api/nothing");
    expect(resp.status).toBe(404);
    expect(await resp.json()).toEqual({ error: "not_found" });
  });
});
