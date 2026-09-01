// 收購同款多件：畫面上是一列＋數量，送出時才變成 N 筆獨立的序號品。
//
// 為什麼展開而不是存一個數量欄：客人帶三頂一樣的帳篷，那是**三件各自獨立的商品**——
// 各有自己的條碼標籤、可以分別賣掉、分別退貨。存成「一列數量 3」會退化成散裝批
// （E 級）的語意，那是另一種東西（一堆共用成本、按件扣減）。
import { describe, expect, it } from "vitest";

import { expandByQty, qtyErrors, rowsPayableTotal } from "@/features/acquisition/quantity";
import type { AcqType, ItemDraft } from "@/features/acquisition/validation";
import { validateDraft } from "@/features/acquisition/validation";

const row = (over: Partial<ItemDraft & { qty: string }> = {}) => ({
  name: "帳篷",
  grade: "A" as const,
  categoryId: 1,
  brandId: null,
  productModelId: null,
  listedPrice: "1000",
  acquisitionCost: "500",
  commissionPct: "",
  qty: "1",
  ...over,
});

describe("數量展開", () => {
  it("數量 3 → 三筆一模一樣的品項", () => {
    const out = expandByQty([row({ qty: "3" })]);

    expect(out).toHaveLength(3);
    expect(out.every((r) => r.name === "帳篷" && r.acquisitionCost === "500")).toBe(true);
  });

  it("數量 1（預設）→ 維持一筆，行為與改動前相同", () => {
    expect(expandByQty([row()])).toHaveLength(1);
  });

  it("多列各自展開，順序不亂（收據與切結書要看得懂）", () => {
    const out = expandByQty([row({ name: "帳篷", qty: "2" }), row({ name: "睡袋", qty: "3" })]);

    expect(out.map((r) => r.name)).toEqual(["帳篷", "帳篷", "睡袋", "睡袋", "睡袋"]);
  });

  it("件數不合法 → 展開成 0 筆，絕不「當作 1 件」", () => {
    // fail closed：一張沒被擋住的壞資料若默默變成一件真的存貨，事後要人工作廢；
    // 展開成 0 筆會讓總額與件數明顯不對，當場就看得出來。
    expect(expandByQty([row({ qty: "abc" })])).toHaveLength(0);
    expect(expandByQty([row({ qty: "0" })])).toHaveLength(0);
    expect(expandByQty([row({ qty: "" })])).toHaveLength(0);
  });

  it("展開後不帶數量欄位——送到後端的是純品項，數量只是輸入介面的事", () => {
    const [first] = expandByQty([row({ qty: "2" })]);

    expect("qty" in first).toBe(false);
  });
});

describe("應付總額隨數量變動", () => {
  it("每件 500 × 3 件 = 1500", () => {
    expect(rowsPayableTotal([row({ acquisitionCost: "500", qty: "3" })])).toBe(1500);
  });

  it("多列相加", () => {
    expect(
      rowsPayableTotal([
        row({ acquisitionCost: "500", qty: "2" }),
        row({ acquisitionCost: "300", qty: "1" }),
      ]),
    ).toBe(1300);
  });

  it("數量填壞時不得默默當成 1——寧可算 0 讓總額明顯不對，也不要少付客人錢", () => {
    expect(rowsPayableTotal([row({ acquisitionCost: "500", qty: "abc" })])).toBe(0);
  });
});

describe("數量驗證", () => {
  it("正整數才合法", () => {
    expect(qtyErrors(0, "1")).toEqual([]);
    expect(qtyErrors(0, "12")).toEqual([]);
  });

  it("0、負數、小數、空白、非數字一律擋下", () => {
    // "1e2" 與 "0x10" 特別重要：JavaScript 的 Number() 會把它們讀成 100 與 16，
    // 只靠數值範圍檢查會讓店員打錯的字串默默變成上百件存貨。
    for (const bad of ["0", "-1", "1.5", "", "  ", "abc", "３", "1e2", "0x10", "+2", "2 3"]) {
      expect(qtyErrors(0, bad), `qty=${bad}`).not.toEqual([]);
    }
  });

  it("上限 99：打錯一個鍵就建出上萬件存貨，比擋下來麻煩得多", () => {
    expect(qtyErrors(0, "99")).toEqual([]);
    expect(qtyErrors(0, "100")).not.toEqual([]);
  });

  it("錯誤訊息指出是第幾列，店員才知道要改哪裡", () => {
    expect(qtyErrors(2, "0")[0]).toContain("第 3 列");
  });
});

// ── 送出閘門（Codex 對抗式審查兩個 High）──
//
// 展開層的 fail closed（不合法就展開成 0 筆）本身沒錯，但它不是閘門：送出前的
// validateDraft 完全沒看件數，於是「畫面顯示紅字、按下去照樣送出、那一列靜默消失」。
// 店員以為收了兩列，實際只建了一列，付給客人的錢也少了。
describe("件數必須擋住送出", () => {
  const draft = (items: (ItemDraft & { qty: string })[], type: AcqType = "BUYOUT") => ({
    type,
    contactId: 1,
    items,
    lot: {
      name: "", categoryId: null, brandId: null, acquisitionCost: "",
      acquisitionBasis: "" as const, totalQty: "", unitPrice: "", label: "",
    },
    payoutMethod: "CASH" as const,
    payoutSplitCash: "",
    sellerIsMember: false,
  });

  it("任何一列件數不合法 → validateDraft 回錯誤（送出被擋）", () => {
    const errors = validateDraft(draft([row(), row({ qty: "0" })]));

    expect(errors.some((e) => e.includes("件數"))).toBe(true);
  });

  it("超過上限也擋（99 件上限必須是真的送出守衛，不只是提示）", () => {
    expect(validateDraft(draft([row({ qty: "100" })])).some((e) => e.includes("件數"))).toBe(true);
  });

  it("全部合法就不擋", () => {
    expect(validateDraft(draft([row({ qty: "3" }), row({ qty: "1" })]))).toEqual([]);
  });

  it("撥款檢查要用「單價 × 件數」的總額，否則合法的拆帳被誤擋", () => {
    // 500 × 3 = 1500，現金 700 是合法的 SPLIT；若總額被誤算成 500，700 會被當成溢付。
    const d = {
      ...draft([row({ acquisitionCost: "500", qty: "3" })]),
      payoutMethod: "SPLIT" as const,
      payoutSplitCash: "700",
      sellerIsMember: true,
    };

    expect(validateDraft(d)).toEqual([]);
  });

  it("寄售不看件數——件數欄只在買斷出現，寄售帶著舊值不該擋人", () => {
    const errors = validateDraft(draft([row({ qty: "3", commissionPct: "50" })], "CONSIGNMENT"));

    expect(errors.filter((e) => e.includes("件數"))).toEqual([]);
  });
});

describe("寄售不得夾帶買斷的件數（Codex 對抗式審查 High）", () => {
  it("非買斷一律當 1 件展開", () => {
    // 買斷填 3 件後切到寄售，件數欄會被隱藏但值還在。若展開照跑，
    // 店員看到一列寄售品、系統實際建了三件——而且畫面上完全沒有線索。
    const rows = [row({ qty: "3" })];

    expect(expandByQty(rows, "CONSIGNMENT")).toHaveLength(1);
    expect(expandByQty(rows, "BUYOUT")).toHaveLength(3);
  });

  it("預設（不指定型別）維持買斷語意，既有呼叫端行為不變", () => {
    expect(expandByQty([row({ qty: "3" })])).toHaveLength(3);
  });

  it("非買斷的合計也只算一件", () => {
    const rows = [row({ acquisitionCost: "500", qty: "3" })];

    expect(rowsPayableTotal(rows, "CONSIGNMENT")).toBe(500);
    expect(rowsPayableTotal(rows, "BUYOUT")).toBe(1500);
  });
});
