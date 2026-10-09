// 「不知道喝什麼」引導推薦的計分（docs/63 §2 M2a；店主 2026-10-09）：每個答案勾的品項各加 1 分，
// 分數高的排前面；同分照勾的先後；售完、下架、看不到的不推；1 主推＋最多 2 備選。
import { describe, expect, it } from "vitest";

import { quizResult } from "../src/client/logic";
import type { MenuExperienceView, MenuItemView, MenuQuizView, MenuSnapshot } from "../src/client/types";

function item(id: number, overrides: Partial<MenuItemView> = {}): MenuItemView {
  return {
    id, name: `品項${id}`, description: null, category_id: 1, unit_price: 100, photo: null,
    available: true, remaining: null, option_groups: [], ...overrides,
  };
}

const EXP: MenuExperienceView = {
  id: 9, item_id: 1, option_ids: [], title: "手沖體驗", tag: null, origin: null, notes: null,
  description: null, includes: [], theme: "peach", art: "peach", effect: "random",
};

const ref = (id: number) => ({ kind: "item" as const, id });

function menu(quiz: MenuQuizView, items: MenuItemView[] = [1, 2, 3, 4, 5].map((id) => item(id))): MenuSnapshot {
  return {
    version: 1, published_at: "2026-10-09T00:00:00Z", store_name: "露坑", font: null,
    categories: [{ id: 1, name: "咖啡" }], items, experiences: [EXP], quiz,
  };
}

const QUIZ: MenuQuizView = {
  questions: [
    { prompt: "想喝什麼？", options: [
      { label: "咖啡", items: [ref(1), ref(2), ref(3)] },
      { label: "甜的", items: [ref(4)] },
    ] },
    { prompt: "什麼味道？", options: [
      { label: "果香", items: [ref(3), { kind: "experience", id: 9 }] },
      { label: "堅果", items: [ref(2)] },
    ] },
  ],
};

const ids = (picks: { kind: string; id: number }[]) => picks.map((p) => `${p.kind}:${p.id}`);

describe("引導推薦計分", () => {
  it("被勾最多次的排第一，其次照勾的先後，最多 1 主推＋2 備選", () => {
    const result = quizResult(menu(QUIZ), [0, 0]);
    expect(ids(result)).toEqual(["item:3", "item:1", "item:2"]);
  });

  it("體驗卡也能被推薦", () => {
    // 甜的（4）＋果香（3、體驗卡 9）：都只被勾 1 次，照勾的先後
    expect(ids(quizResult(menu(QUIZ), [1, 0]))).toEqual(["item:4", "item:3", "experience:9"]);
  });

  it("售完、看不到的品項不推，換下一個", () => {
    const items = [item(1), item(2), item(3, { remaining: 0 }), item(4), item(5)];
    // 3 號售完：原本的第一名不推，由 1、2 與果香的體驗卡補上
    expect(ids(quizResult(menu(QUIZ, items), [0, 0]))).toEqual(["item:1", "item:2", "experience:9"]);
  });

  it("一個都對不上：回空（客人頁改給完整菜單入口，不捏造推薦）", () => {
    const quiz: MenuQuizView = { questions: [{ prompt: "想喝什麼？", options: [
      { label: "咖啡", items: [] }, { label: "甜的", items: [ref(4)] },
    ] }] };
    expect(quizResult(menu(quiz), [0])).toEqual([]);
  });

  it("引用到快照裡已經沒有的品項就略過", () => {
    const quiz: MenuQuizView = { questions: [{ prompt: "想喝什麼？", options: [
      { label: "咖啡", items: [ref(99), ref(5)] }, { label: "甜的", items: [] },
    ] }] };
    expect(ids(quizResult(menu(quiz), [0]))).toEqual(["item:5"]);
  });
});
