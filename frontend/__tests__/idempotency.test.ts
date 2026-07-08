// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";

import {
  canDiscardIdempotencyKey,
  clearPendingAcqIdemKey,
  loadPendingAcqIdemKey,
  pendingAcqIdemKeyServerSnapshot,
  pendingAcqIdemKeySnapshot,
  savePendingAcqIdemKey,
  subscribePendingAcqIdemKey,
} from "@/lib/idempotency";

describe("canDiscardIdempotencyKey（回應遺失/失敗時是否可丟棄凍結的冪等鍵）", () => {
  it("非衝突 4xx（驗證/認證，確定未提交）→ 可丟棄換新鍵", () => {
    for (const s of [400, 401, 403, 404, 422, 499]) {
      expect(canDiscardIdempotencyKey(s)).toBe(true);
    }
  });

  it("409 衝突＝先前已提交的證據 → 不可丟棄（否則改表單再送會重複建單/撥款）", () => {
    expect(canDiscardIdempotencyKey(409)).toBe(false);
  });

  it("5xx＝曖昧（可能已提交）→ 不可丟棄、須沿用重放", () => {
    for (const s of [500, 502, 503, 504]) {
      expect(canDiscardIdempotencyKey(s)).toBe(false);
    }
  });

  it("2xx/3xx 不視為可丟棄的明確錯誤", () => {
    for (const s of [200, 201, 301, 302]) {
      expect(canDiscardIdempotencyKey(s)).toBe(false);
    }
  });
});

describe("待確認收購冪等鍵 localStorage 持久化（跨重掛存活，第十八輪）", () => {
  beforeEach(() => {
    clearPendingAcqIdemKey();
  });

  it("save 後 load 取回同鍵；模擬重掛（重讀）仍在", () => {
    expect(loadPendingAcqIdemKey()).toBeNull();
    savePendingAcqIdemKey("idem-abc");
    expect(loadPendingAcqIdemKey()).toBe("idem-abc");
    // 重掛＝重新讀取 localStorage，鍵仍存活（未簽收購重送沿用同鍵、不重複建單）。
    expect(loadPendingAcqIdemKey()).toBe("idem-abc");
  });

  it("clear 後不再取回", () => {
    savePendingAcqIdemKey("idem-xyz");
    clearPendingAcqIdemKey();
    expect(loadPendingAcqIdemKey()).toBeNull();
  });

  it("localStorage.setItem 丟例外時：save 回 false 但記憶體後備仍讓 load 取回同鍵（第二十輪）", () => {
    const orig = Storage.prototype.setItem;
    Storage.prototype.setItem = () => {
      throw new DOMException("QuotaExceededError");
    };
    try {
      const durable = savePendingAcqIdemKey("mem-fallback-key");
      expect(durable).toBe(false); // 未持久化
      // 關鍵：重試會以 load 取鍵，須回同鍵（記憶體後備）而非 null → 不會鑄新鍵重複建單。
      expect(loadPendingAcqIdemKey()).toBe("mem-fallback-key");
    } finally {
      Storage.prototype.setItem = orig;
    }
    clearPendingAcqIdemKey();
    expect(loadPendingAcqIdemKey()).toBeNull();
  });

  it("save 成功回 true（可持久化）", () => {
    expect(savePendingAcqIdemKey("durable-key")).toBe(true);
    clearPendingAcqIdemKey();
  });

  it("external store：snapshot 反映現值、server snapshot 恆 null、save/clear 通知訂閱者", () => {
    expect(pendingAcqIdemKeyServerSnapshot()).toBeNull(); // SSR/hydration 首次一律 null
    let notified = 0;
    const unsub = subscribePendingAcqIdemKey(() => {
      notified += 1;
    });
    expect(pendingAcqIdemKeySnapshot()).toBeNull();
    savePendingAcqIdemKey("idem-store");
    expect(pendingAcqIdemKeySnapshot()).toBe("idem-store"); // 掛載時即可反映殘留鍵
    expect(notified).toBe(1);
    clearPendingAcqIdemKey();
    expect(pendingAcqIdemKeySnapshot()).toBeNull();
    expect(notified).toBe(2);
    unsub();
    savePendingAcqIdemKey("idem-after-unsub");
    expect(notified).toBe(2); // 取消訂閱後不再通知
    clearPendingAcqIdemKey();
  });
});
