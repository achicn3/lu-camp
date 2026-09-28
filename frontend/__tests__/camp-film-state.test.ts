import { describe, expect, it } from "vitest";
import { FilmPlayback } from "@/features/customer-display/film/state";

describe("客顯短片與營業事件", () => {
  it("簽署中與頁面不可見時停住故事", () => {
    const film = new FilmPlayback();
    film.advance(4);
    film.setMode("hidden");
    film.advance(10);
    expect(film.snapshot().time).toBe(4);
    film.setMode("idle");
    film.advance(1);
    expect(film.snapshot().time).toBe(5);
  });
  it("商品提示合併連續掃描，減量不觸發，初始數量不當新增", () => {
    const film = new FilmPlayback("cart", false, 3);
    film.setItemCount(3);
    expect(film.snapshot().effect).toBeNull();
    film.setItemCount(4);
    film.setItemCount(5);
    expect(film.snapshot().effect?.kind).toBe("item");
    film.advance(2);
    expect(film.snapshot().effect).toBeNull();
    film.setItemCount(2);
    expect(film.snapshot().effect).toBeNull();
  });
  it("付款與簽署有不同回饋，重複快照不重播完成動畫", () => {
    const film = new FilmPlayback();
    film.setMode("paid");
    expect(film.snapshot().effect?.kind).toBe("paid");
    film.advance(1);
    film.setMode("paid");
    expect(film.snapshot().effect?.age).toBe(1);
    film.setMode("hidden");
    expect(film.snapshot().effect).toBeNull();
    film.setMode("celebrate");
    expect(film.snapshot().effect?.kind).toBe("signed");
  });
  it("減少動態效果保留完成狀態，但不播放粒子與故事", () => {
    const film = new FilmPlayback("idle", true);
    const start = film.snapshot().time;
    film.advance(30);
    expect(film.snapshot().time).toBe(start);
    film.setMode("paid");
    expect(film.snapshot().mode).toBe("paid");
    expect(film.snapshot().effect).toBeNull();
  });
  it("結帳期間停住原故事，回到待機平順接續", () => {
    const film = new FilmPlayback();
    film.advance(14);
    film.setMode("cart");
    film.advance(5);
    expect(film.snapshot().time).toBe(14);
    film.setMode("idle");
    film.advance(1);
    expect(film.snapshot().time).toBe(15);
  });
});
