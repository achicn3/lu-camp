export const DURATION = 80;
export const VAN_SCALE = .9;
export const WHEEL_RADIUS = 34 * VAN_SCALE;
// Forty complete wheel revolutions per route: tread, hub and roadside all join exactly.
export const ROUTE = Math.PI * 2 * WHEEL_RADIUS * 40;
export const SPEED = ROUTE / DURATION;
export function sampleDrive(seconds: number) {
  const time = ((seconds % DURATION) + DURATION) % DURATION;
  const phase = time / DURATION;
  const distance = phase * ROUTE;
  return {
    time, near: distance, mid: distance * .5, far: distance * .25,
    foreground: distance * 1.25,
    wheel: phase * 40 * 360,
    bounce: Math.sin(phase * Math.PI * 2 * 120) * 1.1,
    roll: Math.sin(phase * Math.PI * 2 * 40) * .15,
    cloud: Math.sin(phase * Math.PI * 2) * 16,
    light: .5 - .5 * Math.cos(phase * Math.PI * 2),
  };
}
