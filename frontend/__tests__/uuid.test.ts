import { afterEach, describe, expect, it, vi } from "vitest";

import { newIdempotencyKey } from "@/lib/uuid";

afterEach(() => vi.unstubAllGlobals());

describe("newIdempotencyKey", () => {
  it("用 randomUUID（安全情境）", () => {
    vi.stubGlobal("crypto", { randomUUID: () => "uuid-from-secure" });
    expect(newIdempotencyKey()).toBe("uuid-from-secure");
  });

  it("LAN HTTP（無 randomUUID）：以 getRandomValues 產生 v4 格式", () => {
    vi.stubGlobal("crypto", {
      getRandomValues: (a: Uint8Array) => {
        a.fill(0xab);
        return a;
      },
    });
    const key = newIdempotencyKey();
    expect(key).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });

  it("最終後備：無 crypto 仍產生非空鍵", () => {
    vi.stubGlobal("crypto", undefined);
    expect(newIdempotencyKey().length).toBeGreaterThan(0);
  });
});
