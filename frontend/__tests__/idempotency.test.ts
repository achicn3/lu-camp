// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";

import {
  canDiscardIdempotencyKey,
  clearPendingAcqIdemKey,
  clearPendingCatalogCreate,
  clearPersistedIdemKey,
  getOrCreatePersistedIdemKey,
  loadPendingAcqIdemKey,
  loadPendingCatalogCreate,
  pendingAcqIdemKeyServerSnapshot,
  pendingAcqIdemKeySnapshot,
  savePendingAcqIdemKey,
  savePendingCatalogCreate,
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

describe("待確認一般商品建檔持久化", () => {
  const body = {
    sku: null,
    name: "首次採購營繩",
    unit_price: 260,
    reorder_point: 3,
  };

  beforeEach(() => {
    clearPendingCatalogCreate(1);
    clearPendingCatalogCreate(2);
  });

  it("依店別保存鍵與原始 body，重掛後可完整重放", () => {
    savePendingCatalogCreate(1, { key: "catalog-idem-1", body });

    expect(loadPendingCatalogCreate(1)).toEqual({ key: "catalog-idem-1", body });
    expect(JSON.parse(localStorage.getItem("lu-camp.catalog-create-pending-idem.1") ?? "null")).toEqual({
      key: "catalog-idem-1",
      body,
    });
  });

  it("不同店別不會誤用待確認請求，清除也只影響指定店別", () => {
    savePendingCatalogCreate(1, { key: "catalog-idem-1", body });
    savePendingCatalogCreate(2, { key: "catalog-idem-2", body: { ...body, name: "二店營繩" } });

    clearPendingCatalogCreate(1);

    expect(loadPendingCatalogCreate(1)).toBeNull();
    expect(loadPendingCatalogCreate(2)?.key).toBe("catalog-idem-2");
  });
});

describe("getOrCreatePersistedIdemKey（結帳/退貨冪等鍵跨重掛存活；Codex 第二輪 #2/#3）", () => {
  beforeEach(() => {
    clearPersistedIdemKey("pos-checkout");
    clearPersistedIdemKey("return-1");
  });

  it("同 scope 同指紋 → 恆回同鍵（重掛/重整/重掃後沿用，不重扣/不重退）", () => {
    const a = getOrCreatePersistedIdemKey("pos-checkout", "cart-A");
    const b = getOrCreatePersistedIdemKey("pos-checkout", "cart-A");
    expect(a).toBe(b);
  });

  it("指紋變（不同購物車/退貨計畫）→ 換新鍵", () => {
    const a = getOrCreatePersistedIdemKey("pos-checkout", "cart-A");
    const b = getOrCreatePersistedIdemKey("pos-checkout", "cart-B");
    expect(a).not.toBe(b);
  });

  it("跨 scope 不互相干擾（結帳 vs 各銷售退貨）", () => {
    const checkout = getOrCreatePersistedIdemKey("pos-checkout", "same-fp");
    const ret = getOrCreatePersistedIdemKey("return-1", "same-fp");
    expect(checkout).not.toBe(ret);
  });

  it("clear 後同指紋改鑄新鍵（成立後下一筆換新鍵）", () => {
    const a = getOrCreatePersistedIdemKey("return-1", "plan-X");
    clearPersistedIdemKey("return-1");
    const b = getOrCreatePersistedIdemKey("return-1", "plan-X");
    expect(a).not.toBe(b);
  });

  it("持久化至 localStorage（模擬重整後仍取得同鍵）", () => {
    const key = getOrCreatePersistedIdemKey("pos-checkout", "cart-persist");
    const raw = globalThis.localStorage.getItem("lu-camp.pending-idem.pos-checkout");
    expect(raw).not.toBeNull();
    expect(JSON.parse(raw as string).key).toBe(key);
  });
});
