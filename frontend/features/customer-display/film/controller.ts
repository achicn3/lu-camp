import { FilmPainter } from "./draw";
import { FilmPlayback, type SceneMode } from "./state";

export function createFilmController(canvas: HTMLCanvasElement, reduced = false, mode: SceneMode = "idle", count = 0) {
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Canvas 2D unavailable");
  const painter = new FilmPainter(context);
  const film = new FilmPlayback(mode, reduced, count);
  let frame = 0, last = 0, clock = 0, width = 1, height = 1, paused = false, destroyed = false;
  const paint = () => {
    const state = film.snapshot();
    canvas.dataset.filmMode = state.mode;
    canvas.dataset.effect = state.effect?.kind ?? "none";
    canvas.dataset.storyTime = state.time.toFixed(2);
    if (state.mode !== "hidden") painter.draw(state, width, height, clock);
  };
  const stop = () => { cancelAnimationFrame(frame); frame = 0; last = 0; };
  const tick = (now: number) => {
    const dt = last ? Math.min((now - last) / 1000, .1) : 0;
    last = now; clock += dt; film.advance(dt); paint();
    frame = requestAnimationFrame(tick);
  };
  const sync = () => {
    stop();
    if (destroyed) return;
    paint();
    if (document.hidden || film.snapshot().mode === "hidden") return;
    if (!reduced && !paused) frame = requestAnimationFrame(tick);
  };
  const resize = () => {
    const box = canvas.getBoundingClientRect(); width = box.width || 960; height = box.height || 960;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0); paint();
  };
  const observer = new ResizeObserver(resize); observer.observe(canvas);
  document.addEventListener("visibilitychange", sync);
  resize(); sync();
  return {
    setMode(next: SceneMode) { film.setMode(next); sync(); },
    setItemCount(next: number) { film.setItemCount(next); paint(); },
    seek(time: number) { film.seek(time); paint(); },
    pause() { paused = true; stop(); },
    play() { paused = false; sync(); },
    snapshot() { return film.snapshot(); },
    destroy() { destroyed = true; stop(); observer.disconnect(); document.removeEventListener("visibilitychange", sync); },
  };
}
export type FilmController = ReturnType<typeof createFilmController>;
