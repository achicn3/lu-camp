import type { OnlineHours } from "./client/types";

const CLOCK = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;

export function validHours(value: unknown): value is OnlineHours | undefined {
  if (value === undefined) return true; // Older published menus keep manual control.
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const h = value as Record<string, unknown>;
  return Object.keys(h).every(k => ["enabled", "days"].includes(k)) &&
    typeof h.enabled === "boolean" && Array.isArray(h.days) && h.days.length === 7 &&
    h.days.every((d: unknown) => {
      if (d === null) return true;
      if (!d || typeof d !== "object" || Array.isArray(d)) return false;
      const day = d as Record<string, unknown>;
      return Object.keys(day).length === 2 && typeof day.opens === "string" &&
        typeof day.closes === "string" && CLOCK.test(day.opens) && CLOCK.test(day.closes) &&
        day.opens !== day.closes;
    });
}

export function isWithinHours(hours: OnlineHours | undefined, now: number): boolean {
  if (!hours?.enabled) return true;
  // Taiwan local wall clock (UTC+8), independent of Worker/server/browser timezone.
  const local = new Date(now + 8 * 60 * 60 * 1000);
  const day = (local.getUTCDay() + 6) % 7; // Monday first.
  const time = `${String(local.getUTCHours()).padStart(2, "0")}:${String(local.getUTCMinutes()).padStart(2, "0")}`;
  const today = hours.days[day];
  const yesterday = hours.days[(day + 6) % 7];
  return Boolean(
    (today && time >= today.opens && (today.closes < today.opens || time < today.closes)) ||
    (yesterday && yesterday.closes < yesterday.opens && time < yesterday.closes),
  );
}

export async function withinHours(env: Env, storeId: number, now = Date.now()): Promise<boolean> {
  const row = await env.DB.prepare(
    "SELECT json_extract(s.json, '$.hours') AS hours FROM stores_meta m JOIN menu_snapshots s " +
    "ON s.store_id = m.store_id AND s.version = m.menu_version WHERE m.store_id = ?",
  ).bind(storeId).first<{ hours: string | null }>();
  const hours: unknown = row?.hours ? JSON.parse(row.hours) : undefined;
  return validHours(hours) && isWithinHours(hours, now);
}
