import { describe, expect, it } from "vitest";

import {
  SIGNING_KIND_LABELS,
  SIGNING_STATUS_LABELS,
  contentRows,
  refLabel,
} from "@/features/signing/labels";

describe("signing labels", () => {
  it("三種任務類型與三種狀態皆有中文標籤", () => {
    for (const k of ["ACQUISITION_AFFIDAVIT", "STORE_CREDIT_USE", "TRANSACTION_ACK"]) {
      expect(SIGNING_KIND_LABELS[k]).toBeTruthy();
    }
    for (const s of ["PENDING", "SIGNED", "CANCELLED"]) {
      expect(SIGNING_STATUS_LABELS[s]).toBeTruthy();
    }
  });
});

describe("contentRows", () => {
  it("切結內容：品項結構化＋已知鍵中文化＋總額", () => {
    const rows = contentRows({
      items: [{ name: "登山背包", amount: "1200" }],
      total: "1200",
      seller_name: "王小明",
      national_id_masked: "A12***678*",
    });
    expect(rows).toContainEqual({ label: "品項 1", value: "登山背包（$1200）" });
    expect(rows).toContainEqual({ label: "總額", value: "1200" });
    expect(rows).toContainEqual({ label: "簽署人", value: "王小明" });
    expect(rows).toContainEqual({ label: "身分證（遮罩）", value: "A12***678*" });
  });

  it("散裝批：lot 結構化為數量＋計價基準", () => {
    const rows = contentRows({
      items: [{ name: "營繩一批", amount: "800" }],
      total: "800",
      lot: { total_qty: 30, acquisition_basis: "BAG" },
    });
    expect(rows).toContainEqual({ label: "散裝批", value: "數量 30（計價基準 BAG）" });
  });

  it("未知鍵以原鍵名呈現、未知巢狀物件以 JSON 如實呈現（不可默默丟棄）", () => {
    const rows = contentRows({ custom_key: "x", nested: { a: 1 } });
    expect(rows).toContainEqual({ label: "custom_key", value: "x" });
    expect(rows).toContainEqual({ label: "nested", value: '{"a":1}' });
  });
});

describe("refLabel", () => {
  it("ACK 指向銷售單；未回填反向綁定時回 null", () => {
    expect(refLabel("TRANSACTION_ACK", "sale", 123)).toBe("銷售單 #123");
    expect(refLabel("ACQUISITION_AFFIDAVIT", null, null)).toBeNull();
  });

  it("反向綁定：切結→收購單、扣抵確認→銷售單", () => {
    expect(refLabel("ACQUISITION_AFFIDAVIT", null, null, 77, null)).toBe("收購單 #77");
    expect(refLabel("STORE_CREDIT_USE", null, null, null, 88)).toBe("銷售單 #88");
  });
});

describe("store_credit_premium 快照", () => {
  it("溢價物件結構化呈現（率/實發/多得），不再被物件過濾吃掉", () => {
    const rows = contentRows({
      total: "1000",
      store_credit_premium: { rate: "0.12", amount: "1120", extra: "120" },
    });
    const row = rows.find((r) => r.label === "購物金溢價（凍結）");
    expect(row?.value).toContain("12.0%");
    expect(row?.value).toContain("1120");
    expect(row?.value).toContain("120");
  });
});
