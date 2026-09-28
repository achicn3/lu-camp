export type SceneMode = "idle" | "cart" | "paid" | "celebrate" | "hidden";
export type FilmEffect = { kind: "item" | "paid" | "signed"; age: number };
export const FILM_DURATION = 48;
export type FilmSnapshot = { mode: SceneMode; time: number; effect: FilmEffect | null; reduced: boolean; focus: number };

/** 只管理視覺時間；付款與簽署完成必須由客顯的權威狀態送入。 */
export class FilmPlayback {
  private time = 0;
  private effect: FilmEffect | null = null;
  private focus: number;
  constructor(private mode: SceneMode = "idle", private reduced = false, private itemCount = 0) {
    this.time = reduced ? 28 : 0;
    this.focus = mode === "idle" ? 0 : 1;
    if (!reduced && (mode === "paid" || mode === "celebrate")) this.effect = { kind: mode === "paid" ? "paid" : "signed", age: 0 };
  }
  setMode(mode: SceneMode) {
    if (mode === this.mode) return;
    this.mode = mode;
    this.effect = !this.reduced && (mode === "paid" || mode === "celebrate") ? { kind: mode === "paid" ? "paid" : "signed", age: 0 } : null;
    if (this.reduced) this.focus = mode === "idle" ? 0 : 1;
  }
  setItemCount(count: number) {
    if (count > this.itemCount && this.mode === "cart" && !this.reduced) this.effect = { kind: "item", age: 0 };
    this.itemCount = count;
  }
  advance(seconds: number) {
    if (this.mode === "hidden" || this.reduced) return;
    const dt = Math.max(0, seconds);
    if (this.mode === "idle") this.time = (this.time + dt) % FILM_DURATION;
    this.focus += ((this.mode === "idle" ? 0 : 1) - this.focus) * (1 - Math.exp(-dt * 5));
    if (this.effect) {
      this.effect.age += dt;
      if (this.effect.age > (this.effect.kind === "item" ? 1.6 : 4)) this.effect = null;
    }
  }
  seek(seconds: number) { this.time = ((seconds % FILM_DURATION) + FILM_DURATION) % FILM_DURATION; }
  snapshot(): FilmSnapshot { return { mode: this.mode, time: this.time, effect: this.effect ? { ...this.effect } : null, reduced: this.reduced, focus: this.focus }; }
}
